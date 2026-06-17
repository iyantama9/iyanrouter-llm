# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "fastapi",
#   "httpx",
#   "uvicorn",
#   "python-dotenv",
#   "asyncpg",
#   "jinja2",
#   "bcrypt",
# ]
# ///

import uuid
import json
import time
import asyncio
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Body, Cookie, HTTPException, Depends, Query
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx

from config import (
    KIMCHI_BASE_URL, get_current_key, rotate_key, API_KEYS, SSL_KEYFILE, SSL_CERTFILE, PORT,
    START_TIME, total_requests, failover_count, recent_requests, key_statuses,
    add_request_log, add_api_key, remove_api_key, reset_key_status, get_masked_keys,
    SESSION_SECRET, ADMIN_USERNAME, verify_admin_password, init_state_from_db,
    get_paginated_logs,
)
from translator import build_openai_request, to_anthropic_response, stream_as_anthropic, compact_messages, is_context_window_error, to_anthropic_stream_error, estimate_tokens
from database import init_db, close_db
from contextlib import asynccontextmanager

# ── Jinja2 Templates ──
templates = Jinja2Templates(directory="templates")

# ── SSE Broadcaster ──
class SSEBroadcaster:
    def __init__(self):
        self._queues: set = set()

    def connect(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self._queues.add(q)
        return q

    def disconnect(self, q: asyncio.Queue):
        self._queues.discard(q)

    async def broadcast(self, event_type: str, payload: dict):
        data = json.dumps({"type": event_type, "payload": payload})
        dead = set()
        for q in self._queues:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dead.add(q)
        for q in dead:
            self.disconnect(q)

sse_broadcaster = SSEBroadcaster()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await init_state_from_db()
    print("[INIT] Database connected and state loaded")
    yield
    await close_db()
    print("[INIT] Database connection closed")


app = FastAPI(lifespan=lifespan)


# ═══════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════

async def require_auth(session_token: str = Cookie(default=None)):
    if session_token != SESSION_SECRET:
        raise HTTPException(status_code=401, detail="Not authenticated")


# ═══════════════════════════════════════════════════════════════
# HTML ROUTES (Jinja2)
# ═══════════════════════════════════════════════════════════════

@app.get("/login", response_class=HTMLResponse)
async def get_login(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@app.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard(request: Request, user: None = Depends(require_auth)):
    return templates.TemplateResponse(request=request, name="dashboard.html")


# ═══════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.post("/api/login")
async def api_login(payload: dict = Body(...)):
    username = payload.get("username", "")
    password = payload.get("password", "")
    if username == ADMIN_USERNAME and verify_admin_password(password):
        return JSONResponse(
            content={"success": True},
            headers={
                "Set-Cookie": f"session_token={SESSION_SECRET}; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400"
            }
        )
    return JSONResponse(status_code=401, content={"success": False, "message": "Invalid credentials"})


@app.post("/api/logout")
async def api_logout():
    return JSONResponse(
        content={"success": True},
        headers={
            "Set-Cookie": "session_token=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        }
    )


@app.get("/api/status")
async def get_status(user: None = Depends(require_auth)):
    uptime_seconds = int(time.time() - START_TIME)
    hours = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60
    uptime_str = f"{hours}h {minutes}m {seconds}s"
    current_key = get_current_key()
    active_idx = 0
    try:
        active_idx = API_KEYS.index(current_key)
    except ValueError:
        pass
    return {
        "status": "online",
        "uptime": uptime_str,
        "uptime_seconds": uptime_seconds,
        "total_requests": total_requests,
        "failover_count": failover_count,
        "active_key_index": active_idx,
        "total_keys": len(API_KEYS),
        "keys": get_masked_keys(),
        "recent_requests": recent_requests
    }


@app.get("/api/logs")
async def api_logs(
    user: None = Depends(require_auth),
    page: int = Query(1, ge=1),
    per_page: int = Query(15, ge=5, le=100),
    search: str = Query(""),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("DESC")
):
    return await get_paginated_logs(page, per_page, search, sort_by, sort_order)


@app.post("/api/keys")
async def add_key_endpoint(payload: dict = Body(...), user: None = Depends(require_auth)):
    key = payload.get("key", "")
    success, msg = add_api_key(key)
    if success:
        # Push update via SSE
        await sse_broadcaster.broadcast("status", await _build_status_dict())
    return {"success": success, "message": msg}


@app.delete("/api/keys")
async def remove_key_endpoint(payload: dict = Body(...), user: None = Depends(require_auth)):
    prefix = payload.get("key_prefix", "")
    success, msg = remove_api_key(prefix)
    if success:
        await sse_broadcaster.broadcast("status", await _build_status_dict())
    return {"success": success, "message": msg}


@app.post("/api/keys/reset")
async def reset_key_endpoint(payload: dict = Body(...), user: None = Depends(require_auth)):
    prefix = payload.get("key_prefix", "")
    success, msg = reset_key_status(prefix)
    if success:
        await sse_broadcaster.broadcast("status", await _build_status_dict())
    return {"success": success, "message": msg}


@app.post("/v1/messages/count_tokens")
async def count_tokens(body: dict = Body(...)):
    tokens = estimate_tokens(body)
    return {"input_tokens": tokens}


# ═══════════════════════════════════════════════════════════════
# SSE ENDPOINT
# ═══════════════════════════════════════════════════════════════

async def _build_status_dict():
    uptime_seconds = int(time.time() - START_TIME)
    current_key = get_current_key()
    active_idx = 0
    try:
        active_idx = API_KEYS.index(current_key)
    except ValueError:
        pass
    return {
        "status": "online",
        "uptime_seconds": uptime_seconds,
        "total_requests": total_requests,
        "failover_count": failover_count,
        "active_key_index": active_idx,
        "total_keys": len(API_KEYS),
        "keys": get_masked_keys(),
        "recent_requests": recent_requests
    }


@app.get("/api/sse")
async def sse_endpoint(request: Request, user: None = Depends(require_auth)):
    async def event_generator() -> AsyncGenerator[str, None]:
        q = sse_broadcaster.connect()
        try:
            # Send initial status
            status = await _build_status_dict()
            yield f"data: {json.dumps({'type': 'status', 'payload': status})}\n\n"

            # Send new events as they arrive
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield ":keepalive\n\n"
        finally:
            sse_broadcaster.disconnect(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ═══════════════════════════════════════════════════════════════
# PROXY ENDPOINT
# ═══════════════════════════════════════════════════════════════

@app.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()
    model = body.get("model", "kimi-k2.6")
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    openai_req = build_openai_request(body)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "kimchi/0.2.0",
    }

    # Auto-compacting: try original → keep 20 → keep 6 messages on context overflow
    original_messages = openai_req["messages"][:]
    compact_levels = [None, 20, 6]

    if openai_req.get("stream"):
        async def generate():
            last_error_status = 429
            last_error_content = {"error": {"message": "All configured API keys are rate limited or unauthorized."}}

            for c_idx, compact_level in enumerate(compact_levels):
                if compact_level is not None:
                    openai_req["messages"] = compact_messages(original_messages, keep_last=compact_level)
                    print(f"[LOG] Auto-compacting context → keeping last {compact_level} messages ({len(openai_req['messages'])} total)")

                context_window_hit = False
                rotated_occurred = False

                for attempt in range(len(API_KEYS)):
                    current_key = get_current_key()
                    headers["Authorization"] = f"Bearer {current_key}"
                    start_req_time = time.time()
                    async with httpx.AsyncClient(timeout=300) as client:
                        try:
                            has_yielded = False
                            async with client.stream(
                                "POST",
                                f"{KIMCHI_BASE_URL}/chat/completions",
                                headers=headers,
                                json=openai_req,
                            ) as resp:
                                if resp.status_code in (401, 402, 429, 500, 502, 503, 504):
                                    rotated_occurred = True
                                    add_request_log(model, resp.status_code, current_key, True, int((time.time() - start_req_time) * 1000))
                                    rotate_key()
                                    last_error_status = resp.status_code
                                    try:
                                        await resp.aread()
                                        last_error_content = resp.json()
                                    except Exception:
                                        last_error_content = {"error": f"HTTP {resp.status_code} error body not readable"}
                                    continue

                                if resp.status_code != 200:
                                    try:
                                        await resp.aread()
                                        err_data = resp.json()
                                    except Exception:
                                        err_data = {"error": f"HTTP {resp.status_code} error body not readable"}
                                    # Auto-compact on context window error
                                    if resp.status_code == 400 and is_context_window_error(err_data) and c_idx < len(compact_levels) - 1:
                                        print(f"[LOG] Context window exceeded, auto-compacting...")
                                        context_window_hit = True
                                        break
                                    add_request_log(model, resp.status_code, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                                    yield f"event: error\ndata: {json.dumps(to_anthropic_stream_error(err_data))}\n\n"
                                    return

                                async for chunk in stream_as_anthropic(resp, model, msg_id):
                                    has_yielded = True
                                    yield chunk
                                add_request_log(model, 200, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                                # Broadcast log update via SSE
                                await sse_broadcaster.broadcast("log", recent_requests[0] if recent_requests else {})
                                return
                        except Exception as e:
                            print(f"[STREAM ERROR] Exception during attempt {attempt} (key: {current_key[:10]}...): {type(e).__name__}: {str(e)}")
                            import traceback; traceback.print_exc()
                            if has_yielded or attempt == len(API_KEYS) - 1:
                                add_request_log(model, 500, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                                if not has_yielded:
                                    yield f"event: error\ndata: {json.dumps(to_anthropic_stream_error(str(e)))}\n\n"
                                return
                            rotated_occurred = True
                            add_request_log(model, 500, current_key, True, int((time.time() - start_req_time) * 1000))
                            rotate_key()
                            last_error_status = 500
                            last_error_content = {"error": str(e)}

                if context_window_hit:
                    continue

                yield f"event: error\ndata: {json.dumps(to_anthropic_stream_error(last_error_content))}\n\n"
                return

            # All compaction levels exhausted
            yield f"event: error\ndata: {json.dumps(to_anthropic_stream_error('Konteks terlalu panjang bahkan setelah auto-compact. Silakan mulai percakapan baru.'))}\n\n"
            return

        return StreamingResponse(generate(), media_type="text/event-stream")

    # Non-streaming path with auto-compacting
    last_error_status = 429
    last_error_content = {"error": {"message": "All configured API keys are rate limited or unauthorized."}}

    for c_idx, compact_level in enumerate(compact_levels):
        if compact_level is not None:
            openai_req["messages"] = compact_messages(original_messages, keep_last=compact_level)
            print(f"[LOG] Auto-compacting context → keeping last {compact_level} messages ({len(openai_req['messages'])} total)")

        context_window_hit = False
        rotated_occurred = False

        for attempt in range(len(API_KEYS)):
            current_key = get_current_key()
            headers["Authorization"] = f"Bearer {current_key}"
            start_req_time = time.time()
            try:
                async with httpx.AsyncClient(timeout=300) as client:
                    resp = await client.post(
                        f"{KIMCHI_BASE_URL}/chat/completions",
                        headers=headers,
                        json=openai_req,
                    )
                if resp.status_code in (401, 402, 429, 500, 502, 503, 504):
                    rotated_occurred = True
                    add_request_log(model, resp.status_code, current_key, True, int((time.time() - start_req_time) * 1000))
                    rotate_key()
                    last_error_status = resp.status_code
                    try:
                        last_error_content = resp.json()
                    except Exception:
                        last_error_content = {"error": resp.text}
                    continue
                if resp.status_code != 200:
                    try:
                        err_json = resp.json()
                    except Exception:
                        err_json = {"error": resp.text}
                    # Auto-compact on context window error
                    if resp.status_code == 400 and is_context_window_error(err_json) and c_idx < len(compact_levels) - 1:
                        print(f"[LOG] Context window exceeded, auto-compacting...")
                        context_window_hit = True
                        break
                    add_request_log(model, resp.status_code, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                    return JSONResponse(status_code=resp.status_code, content=err_json)
                add_request_log(model, 200, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                await sse_broadcaster.broadcast("log", recent_requests[0] if recent_requests else {})
                return JSONResponse(to_anthropic_response(resp.json(), model, msg_id))
            except Exception as e:
                print(f"[LOG] Request attempt {attempt} with key {current_key[:10]}... failed: {type(e).__name__}: {str(e)}")
                if attempt == len(API_KEYS) - 1:
                    import traceback
                    traceback.print_exc()
                    add_request_log(model, 500, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                    return JSONResponse(status_code=500, content={"error": str(e)})
                rotated_occurred = True
                add_request_log(model, 500, current_key, True, int((time.time() - start_req_time) * 1000))
                rotate_key()
                last_error_status = 500
                last_error_content = {"error": str(e)}

        if context_window_hit:
            continue

        return JSONResponse(status_code=last_error_status, content=last_error_content)

    # All compaction levels exhausted
    return JSONResponse(
        status_code=400,
        content={"error": {"message": "Konteks terlalu panjang bahkan setelah auto-compact. Silakan mulai percakapan baru."}}
    )


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn, os
    if PORT == 443:
        import http.server, socketserver, threading

        class RedirectHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                host = self.headers.get('Host', 'routers.iyantama.tech')
                self.send_response(301)
                self.send_header('Location', f'https://{host}{self.path}')
                self.end_headers()
            def do_POST(self):
                self.do_GET()
            def do_HEAD(self):
                self.do_GET()
            def log_message(self, format, *args):
                pass

        def start_redirect_server():
            try:
                class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
                    allow_reuse_address = True
                server = ThreadedTCPServer(("0.0.0.0", 80), RedirectHandler)
                print("[LOG] Starting HTTP-to-HTTPS redirect server on port 80...")
                server.serve_forever()
            except Exception as e:
                print(f"[ERROR] Failed to start redirect server on port 80: {e}")

        threading.Thread(target=start_redirect_server, daemon=True).start()

    if os.path.exists(SSL_KEYFILE) and os.path.exists(SSL_CERTFILE):
        uvicorn.run(app, host="0.0.0.0", port=PORT, ssl_keyfile=SSL_KEYFILE, ssl_certfile=SSL_CERTFILE)
    else:
        uvicorn.run(app, host="0.0.0.0", port=PORT)
