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

# NOTE: total_requests / failover_count are NOT imported here.
# They are read via config_module.* inside async functions to avoid
# stale module-level binding (int is immutable → import → by-value copy).
import config as config_module
from config import (
    DEFAULT_UPSTREAM_URL, CAVOTI_API_KEY, CAVOTI_BASE_URL, BLUESMINDS_API_KEY, BLUESMINDS_BASE_URL, ROUTER_PASSWORD, get_current_key, rotate_key, API_KEYS, SSL_KEYFILE, SSL_CERTFILE, PORT,
    KIMCHI_MODELS, CAVOTI_MODELS, BLUESMINDS_MODELS, CV_API_KEYS, BM_API_KEYS,
    get_current_cv_key, rotate_cv_key, get_current_bm_key, rotate_bm_key,
    recent_requests, key_statuses, ROUTER_DOMAIN,
    add_request_log, add_api_key, remove_api_key, reset_key_status, get_masked_keys, set_active_key,
    SESSION_SECRET, ADMIN_USERNAME, verify_admin_password, init_state_from_db,
    get_paginated_logs,
)
from translator import build_openai_request, to_anthropic_response, stream_as_anthropic, compact_messages, is_context_window_error, parse_input_tokens_from_error, to_anthropic_stream_error, estimate_tokens
from database import init_db, close_db, get_chat_sessions, get_chat_session, create_chat_session, update_chat_session, delete_chat_session, get_chat_messages, save_chat_message
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

@app.get("/", response_class=RedirectResponse)
async def get_root():
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def get_login(request: Request, session_token: str = Cookie(default=None)):
    if session_token == SESSION_SECRET:
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html")


@app.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard(request: Request, session_token: str = Cookie(default=None)):
    if session_token != SESSION_SECRET:
        return RedirectResponse(url="/login", status_code=303)
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
                "Set-Cookie": f"session_token={SESSION_SECRET}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000"
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
    uptime_seconds = int(time.time() - config_module.START_TIME)
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
        "total_requests": config_module.total_requests,
        "failover_count": config_module.failover_count,
        "total_tokens": config_module.total_tokens,
        "active_key_index": active_idx,
        "total_keys": len(API_KEYS) + len(CV_API_KEYS) + len(BM_API_KEYS),
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
    key = payload.get("key", "").strip()
    key_type = payload.get("type", "auto")
    
    # Sophisticated Auto-Detect using endpoint probing
    if key_type == "auto" and key.startswith("sk-"):
        import httpx
        async with httpx.AsyncClient(verify=False) as client:
            # Check Cavoti
            try:
                r = await client.get(f"{CAVOTI_BASE_URL}/models", headers={"Authorization": f"Bearer {key}"}, timeout=3.0)
                if r.status_code == 200:
                    key_type = "cv"
            except Exception:
                pass
            
            # Check Bluesminds
            if key_type == "auto":
                try:
                    r = await client.get(f"{BLUESMINDS_BASE_URL}/models", headers={"Authorization": f"Bearer {key}"}, timeout=3.0)
                    if r.status_code == 200:
                        key_type = "bm"
                except Exception:
                    pass

    success, msg = add_api_key(key, key_type)
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


@app.post("/api/keys/set_active")
async def api_set_active_key(payload: dict = Body(...), user: None = Depends(require_auth)):
    prefix = payload.get("key_prefix", "")
    provider = payload.get("provider", None)
    success, msg = set_active_key(prefix, provider)
    if success:
        await sse_broadcaster.broadcast("status", await _build_status_dict())
    return {"success": success, "message": msg}


@app.get("/api/models")
async def api_get_models(user: None = Depends(require_auth)):
    return {
        "kimchi": [f"kc/{m}" for m in KIMCHI_MODELS],

        "cavoti": [f"cv/{m}" for m in CAVOTI_MODELS],
        "bluesminds": [f"bm/{m}" for m in BLUESMINDS_MODELS],
    }


@app.get("/api/playground/sessions")
async def api_get_sessions(user: None = Depends(require_auth)):
    rows = await get_chat_sessions()
    return [{"id": r["id"], "name": r["name"], "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None} for r in rows]

@app.post("/api/playground/sessions")
async def api_create_session(request: Request, user: None = Depends(require_auth)):
    payload = await request.json()
    name = payload.get("name", "New Chat")
    row = await create_chat_session(name)
    return {"id": row["id"], "name": row["name"]}

@app.put("/api/playground/sessions/{session_id}")
async def api_update_session(session_id: int, request: Request, user: None = Depends(require_auth)):
    payload = await request.json()
    name = payload.get("name")
    if name:
        await update_chat_session(session_id, name)
    return {"success": True}

@app.delete("/api/playground/sessions/{session_id}")
async def api_delete_session(session_id: int, user: None = Depends(require_auth)):
    await delete_chat_session(session_id)
    return {"success": True}

@app.get("/api/playground/sessions/{session_id}/messages")
async def api_get_session_messages(session_id: int, user: None = Depends(require_auth)):
    rows = await get_chat_messages(session_id)
    return [{"id": r["id"], "role": r["role"], "content": r["content"]} for r in rows]

@app.post("/api/playground/chat")
async def api_playground_chat(request: Request, user: None = Depends(require_auth)):
    payload = await request.json()
    session_id = payload.pop("session_id", None)
    
    # Save user message if session exists
    if session_id and payload.get("messages"):
        last_msg = payload["messages"][-1]
        if last_msg["role"] == "user":
            content_str = last_msg["content"]
            if isinstance(content_str, list):
                content_str = json.dumps(content_str)
            await save_chat_message(session_id, "user", str(content_str))

    url = f"http://127.0.0.1:{PORT}/v1/messages"
    headers = {"Authorization": f"Bearer {ROUTER_PASSWORD}", "Content-Type": "application/json"}
    
    if payload.get("stream", False):
        async def stream_generator():
            full_reply = ""
            async with httpx.AsyncClient() as client:
                async with client.stream("POST", url, headers=headers, json=payload, timeout=300.0) as response:
                    async for chunk in response.aiter_raw():
                        yield chunk
                        # Try to parse chunk for saving to DB
                        try:
                            decoded = chunk.decode('utf-8')
                            for line in decoded.split('\n'):
                                if line.startswith('data: ') and line != 'data: [DONE]':
                                    d = json.loads(line[6:])
                                    # Handle OpenAI format (fallback)
                                    if d.get("choices") and d["choices"][0].get("delta", {}).get("content"):
                                        full_reply += d["choices"][0]["delta"]["content"]
                                    # Handle Anthropic format
                                    elif d.get("type") == "content_block_delta" and d.get("delta", {}).get("text"):
                                        full_reply += d["delta"]["text"]
                        except:
                            pass
            if session_id and full_reply:
                await save_chat_message(session_id, "assistant", full_reply)

        return StreamingResponse(stream_generator(), media_type="text/event-stream")
    else:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=payload, timeout=300.0)
            data = resp.json()
            reply_text = ""
            if "choices" in data and data["choices"]:
                reply_text = data["choices"][0].get("message", {}).get("content", "")
            elif "content" in data and isinstance(data["content"], list):
                reply_text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
                
            if session_id and reply_text:
                await save_chat_message(session_id, "assistant", reply_text)
            return JSONResponse(content=data, status_code=resp.status_code)


@app.post("/v1/messages/count_tokens")
async def count_tokens(body: dict = Body(...)):
    tokens = estimate_tokens(body)
    return {"input_tokens": tokens}


# ═══════════════════════════════════════════════════════════════
# SSE ENDPOINT
# ═══════════════════════════════════════════════════════════════

async def _build_status_dict():
    uptime_seconds = int(time.time() - config_module.START_TIME)
    current_key = get_current_key()
    active_idx = 0
    try:
        active_idx = API_KEYS.index(current_key)
    except ValueError:
        pass
    return {
        "status": "online",
        "uptime_seconds": uptime_seconds,
        "total_requests": config_module.total_requests,
        "failover_count": config_module.failover_count,
        "total_tokens": config_module.total_tokens,
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
# MODELS ENDPOINT
# ═══════════════════════════════════════════════════════════════

@app.get("/v1/models")
@app.get("/models")
async def list_models(request: Request):
    auth_header = request.headers.get("Authorization")
    if ROUTER_PASSWORD and auth_header != f"Bearer {ROUTER_PASSWORD}":
        return JSONResponse(status_code=401, content={"error": {"message": "Invalid router password."}})
        
    models = []
    
    # Add Kimchi models with kc/ prefix
    for m in KIMCHI_MODELS:
        models.append(f"kc/{m}")
        

    # Add Cavoti models with cv/ prefix
    for m in CAVOTI_MODELS:
        models.append(f"cv/{m}")
    
    data = []
    for m in models:
        data.append({
            "id": m,
            "object": "model",
            "created": 1700000000,
            "owned_by": "iyan-router"
        })
        
    return JSONResponse(content={"object": "list", "data": data})


# ═══════════════════════════════════════════════════════════════
# PROXY ENDPOINT
# ═══════════════════════════════════════════════════════════════

@app.post("/v1/messages")
async def messages(request: Request):
    auth_header = request.headers.get("Authorization")
    if ROUTER_PASSWORD and auth_header != f"Bearer {ROUTER_PASSWORD}":
        return JSONResponse(status_code=401, content={"error": {"message": "Invalid router password."}})
        
    payload = await request.json()
    provider = "kc"
    if payload.get("model"):
        if payload["model"].startswith("cv/") or payload["model"] in CAVOTI_MODELS:
            provider = "cv"
        elif payload["model"].startswith("bm/") or payload["model"] in BLUESMINDS_MODELS:
            provider = "bm"
        elif payload["model"].startswith("kc/") or payload["model"] in KIMCHI_MODELS:
            provider = "kc"
            
    # Clean model prefix
    for prefix in ("kc/", "cv/", "bm/"):
        if payload.get("model", "").startswith(prefix):
            payload["model"] = payload["model"][3:]
            break

    if provider == "cv":
        upstream_base_url = CAVOTI_BASE_URL
        log_model = f"cv/{payload['model']}"
    elif provider == "bm":
        upstream_base_url = BLUESMINDS_BASE_URL
        log_model = f"bm/{payload['model']}"
    else:
        upstream_base_url = DEFAULT_UPSTREAM_URL
        log_model = f"kc/{payload['model']}"
        
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    upstream_req = build_openai_request(payload, provider=provider)
    upstream_endpoint = f"{upstream_base_url}/chat/completions"
        
    input_tokens = estimate_tokens(payload)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "kimchi/0.2.0",
    }

    # Auto-compacting: try original → keep 20 → keep 6 messages on context overflow
    original_messages = upstream_req["messages"][:]
    compact_levels = [None, 20, 6]

    if provider == "cv":
        api_keys_to_use = CV_API_KEYS
    elif provider == "bm":
        api_keys_to_use = BM_API_KEYS
    else:
        api_keys_to_use = API_KEYS
        
    if not api_keys_to_use:
        # Fallback in case list is totally empty (e.g., config error)
        if provider == "cv" and CAVOTI_API_KEY:
            api_keys_to_use = [CAVOTI_API_KEY]
        elif provider == "bm" and BLUESMINDS_API_KEY:
            api_keys_to_use = [BLUESMINDS_API_KEY]
        else:
            return JSONResponse(status_code=500, content={"error": "No upstream API keys available"})

    if upstream_req.get("stream"):
        async def generate():
            last_error_status = 429
            last_error_content = {"error": {"message": "All configured API keys are rate limited or unauthorized."}}

            for c_idx, compact_level in enumerate(compact_levels):
                if compact_level is not None:
                    upstream_req["messages"] = compact_messages(original_messages, keep_last=compact_level)
                    print(f"[LOG] Auto-compacting context → keeping last {compact_level} messages ({len(upstream_req['messages'])} total)")

                context_window_hit = False
                rotated_occurred = False

                for attempt in range(len(api_keys_to_use)):
                    if provider == "cv":
                        current_key = get_current_cv_key()
                    elif provider == "bm":
                        current_key = get_current_bm_key()
                    else:
                        current_key = get_current_key()
                        
                    headers["Authorization"] = f"Bearer {current_key}"
                        
                    start_req_time = time.time()
                    async with httpx.AsyncClient(timeout=300) as client:
                        try:
                            has_yielded = False
                            async with client.stream(
                                "POST",
                                upstream_endpoint,
                                headers=headers,
                                json=upstream_req,
                            ) as resp:
                                if resp.status_code in (401, 402, 429, 500, 502, 503, 504):
                                    err_data = None
                                    try:
                                        await resp.aread()
                                        err_data = resp.json()
                                    except Exception:
                                        err_data = {"error": f"HTTP {resp.status_code} error body not readable"}
                                    
                                    if err_data and is_context_window_error(err_data):
                                        if c_idx < len(compact_levels) - 1:
                                            print(f"[LOG] Context window exceeded (status {resp.status_code}), auto-compacting...")
                                            context_window_hit = True
                                            break
                                        else:
                                            print(f"[LOG] Context window exceeded after all compactions (status {resp.status_code}). Returning 400 without key rotation.")
                                            add_request_log(log_model, 400, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                                            yield f"event: error\ndata: {json.dumps(to_anthropic_stream_error(err_data))}\n\n"
                                            return

                                    rotated_occurred = True
                                    add_request_log(log_model, resp.status_code, current_key, True, int((time.time() - start_req_time) * 1000))
                                    if provider == "kc":
                                        rotate_key()

                                    elif provider == "cv":
                                        rotate_cv_key()
                                    elif provider == "bm":
                                        rotate_bm_key()
                                    last_error_status = resp.status_code
                                    last_error_content = err_data or {"error": f"HTTP {resp.status_code} error"}
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
                                    add_request_log(log_model, resp.status_code, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                                    yield f"event: error\ndata: {json.dumps(to_anthropic_stream_error(err_data))}\n\n"
                                    return

                                async for chunk in stream_as_anthropic(resp, log_model, msg_id, input_tokens):
                                    has_yielded = True
                                    yield chunk
                                add_request_log(log_model, 200, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000), input_tokens, 0)
                                # Broadcast log update via SSE
                                await sse_broadcaster.broadcast("log", recent_requests[0] if recent_requests else {})
                                return
                        except Exception as e:
                            print(f"[STREAM ERROR] Exception during attempt {attempt} (key: {current_key[:10]}...): {type(e).__name__}: {str(e)}")
                            import traceback; traceback.print_exc()
                            
                            # Check if this is a context window error to trigger auto-compacting rather than key rotation
                            if is_context_window_error(str(e)):
                                if c_idx < len(compact_levels) - 1:
                                    print(f"[LOG] Context window exceeded (parsed from stream exception), triggering auto-compacting...")
                                    context_window_hit = True
                                    break
                                else:
                                    print(f"[LOG] Context window exceeded after all compactions. Returning 400 without key rotation.")
                                    add_request_log(log_model, 400, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                                    if not has_yielded:
                                        yield f"event: error\ndata: {json.dumps(to_anthropic_stream_error('Konteks terlalu panjang bahkan setelah auto-compact. Silakan mulai percakapan baru.'))}\n\n"
                                    return

                            if has_yielded or attempt == len(api_keys_to_use) - 1:
                                add_request_log(log_model, 500, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                                if not has_yielded:
                                    yield f"event: error\ndata: {json.dumps(to_anthropic_stream_error(str(e)))}\n\n"
                                return
                            rotated_occurred = True
                            add_request_log(log_model, 500, current_key, True, int((time.time() - start_req_time) * 1000))
                            if provider == "kc":
                                rotate_key()

                            elif provider == "cv":
                                rotate_cv_key()
                            elif provider == "bm":
                                rotate_bm_key()
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
            upstream_req["messages"] = compact_messages(original_messages, keep_last=compact_level)
            print(f"[LOG] Auto-compacting context → keeping last {compact_level} messages ({len(upstream_req['messages'])} total)")

        context_window_hit = False
        rotated_occurred = False

        for attempt in range(len(api_keys_to_use)):
            if provider == "cv":
                current_key = get_current_cv_key()
            elif provider == "bm":
                current_key = get_current_bm_key()
            else:
                current_key = get_current_key()
                
            if provider in ("cv", "bm"):
                headers["x-api-key"] = current_key
                headers["anthropic-version"] = "2023-06-01"
                if "Authorization" in headers:
                    del headers["Authorization"]
            else:
                headers["Authorization"] = f"Bearer {current_key}"
                
            start_req_time = time.time()
            try:
                async with httpx.AsyncClient(timeout=300) as client:
                    resp = await client.post(
                        upstream_endpoint,
                        headers=headers,
                        json=upstream_req,
                    )
                if resp.status_code in (401, 402, 429, 500, 502, 503, 504):
                    err_json = None
                    try:
                        err_json = resp.json()
                    except Exception:
                        err_json = {"error": resp.text}
                    
                    if err_json and is_context_window_error(err_json):
                        if c_idx < len(compact_levels) - 1:
                            print(f"[LOG] Context window exceeded (status {resp.status_code} non-stream), auto-compacting...")
                            context_window_hit = True
                            break
                        else:
                            print(f"[LOG] Context window exceeded after all compactions (status {resp.status_code} non-stream). Returning 400 without key rotation.")
                            add_request_log(log_model, 400, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                            return JSONResponse(status_code=400, content=err_json)

                    rotated_occurred = True
                    add_request_log(log_model, resp.status_code, current_key, True, int((time.time() - start_req_time) * 1000))
                    if provider == "kc":
                        rotate_key()

                    elif provider == "cv":
                        rotate_cv_key()
                    elif provider == "bm":
                        rotate_bm_key()
                    last_error_status = resp.status_code
                    last_error_content = err_json or {"error": resp.text}
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
                    add_request_log(log_model, resp.status_code, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                    return JSONResponse(status_code=resp.status_code, content=err_json)
                # Extract output tokens from upstream response
                openai_resp = resp.json()
                anthropic_resp = to_anthropic_message(openai_resp, upstream_req)
                
                # Compute token usage if not provided
                usage = anthropic_resp.get("usage", {})
                output_tokens = usage.get("output_tokens", 0)
                add_request_log(log_model, 200, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000), input_tokens, output_tokens)
                await sse_broadcaster.broadcast("log", recent_requests[0] if recent_requests else {})
                return JSONResponse(anthropic_resp)
            except Exception as e:
                print(f"[LOG] Request attempt {attempt} with key {current_key[:10]}... failed: {type(e).__name__}: {str(e)}")
                
                # Check if this is a context window error
                if is_context_window_error(str(e)):
                    if c_idx < len(compact_levels) - 1:
                        print(f"[LOG] Context window exceeded (parsed from non-stream exception), triggering auto-compacting...")
                        context_window_hit = True
                        break
                    else:
                        print(f"[LOG] Context window exceeded after all compactions (non-stream). Returning 400 without key rotation.")
                        add_request_log(log_model, 400, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                        return JSONResponse(
                            status_code=400,
                            content={"error": {"message": "Konteks terlalu panjang bahkan setelah auto-compact. Silakan mulai percakapan baru."}}
                        )

                if attempt == len(api_keys_to_use) - 1:
                    import traceback
                    traceback.print_exc()
                    add_request_log(log_model, 500, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                    return JSONResponse(status_code=500, content={"error": str(e)})
                rotated_occurred = True
                add_request_log(log_model, 500, current_key, True, int((time.time() - start_req_time) * 1000))
                if provider == "kc":
                    rotate_key()

                elif provider == "cv":
                    rotate_cv_key()
                elif provider == "bm":
                    rotate_bm_key()
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
                host = self.headers.get('Host', ROUTER_DOMAIN)
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
