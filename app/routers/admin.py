import json
import time
from typing import AsyncGenerator

from fastapi import APIRouter, Request, Body, Cookie, Depends, Query, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import httpx

import app.config as config_module
from app.config import (
    KIMCHI_BASE_URL, CAVOTI_BASE_URL, BLUESMINDS_BASE_URL, NARA_BASE_URL, DAHL_BASE_URL,
    QWEN_CLOUD_BASE_URL, MARKETKU_BASE_URL, ATOMESUS_BASE_URL, WEIZE_BASE_URL, ROUTER_PASSWORD,
    recent_requests,
    add_api_key, remove_api_key, reset_key_status, get_masked_keys, set_active_key,
    SESSION_SECRET, ADMIN_USERNAME, verify_admin_password, get_paginated_logs,
)
from app.sse import sse_broadcaster
from app.database import fetch, fetch_one, create_router_api_key, get_router_api_keys, delete_router_api_key


router = APIRouter()
templates = Jinja2Templates(directory="templates")


_login_attempts: dict[str, list[float]] = {}
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_WINDOW_SECONDS = 60


def _check_login_rate_limit(ip: str) -> bool:
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < _LOGIN_WINDOW_SECONDS]
    _login_attempts[ip] = attempts
    return len(attempts) < _LOGIN_MAX_ATTEMPTS


def _record_login_attempt(ip: str):
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    attempts.append(now)
    _login_attempts[ip] = attempts


async def require_auth(session_token: str = Cookie(default=None)):
    if session_token != SESSION_SECRET:
        raise HTTPException(status_code=401, detail="Not authenticated")


async def _build_status_dict():
    uptime_seconds = int(time.time() - config_module.START_TIME)
    hours = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60
    uptime_str = f"{hours}h {minutes}m {seconds}s"
    _all_keys = get_masked_keys()
    available_keys = sum(1 for k in _all_keys if k['status'] in ('Active', 'Standby'))
    return {
        "status": "online",
        "uptime": uptime_str,
        "uptime_seconds": uptime_seconds,
        "total_requests": config_module.total_requests,
        "failover_count": config_module.failover_count,
        "total_tokens": config_module.total_tokens,
        "available_keys": available_keys,
        "total_keys": len(_all_keys),
        "keys": _all_keys,
        "recent_requests": recent_requests
    }


@router.get("/", response_class=RedirectResponse)
async def get_root():
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def get_login(request: Request, session_token: str = Cookie(default=None)):
    if session_token == SESSION_SECRET:
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html")


@router.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard(request: Request, session_token: str = Cookie(default=None)):
    if session_token != SESSION_SECRET:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request=request, name="dashboard.html")


@router.post("/api/login")
async def api_login(request: Request, payload: dict = Body(...)):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_login_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"success": False, "message": "Too many login attempts. Try again later."})
    username = payload.get("username", "")
    password = payload.get("password", "")
    if username == ADMIN_USERNAME and verify_admin_password(password):
        return JSONResponse(
            content={"success": True},
            headers={
                "Set-Cookie": f"session_token={SESSION_SECRET}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=2592000"
            }
        )
    _record_login_attempt(client_ip)
    return JSONResponse(status_code=401, content={"success": False, "message": "Invalid credentials"})


@router.post("/api/logout")
async def api_logout():
    return JSONResponse(
        content={"success": True},
        headers={
            "Set-Cookie": "session_token=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        }
    )


@router.get("/api/status")
async def get_status(user: None = Depends(require_auth)):
    return await _build_status_dict()


@router.get("/api/logs")
async def api_logs(
    user: None = Depends(require_auth),
    page: int = Query(1, ge=1),
    per_page: int = Query(15, ge=5, le=100),
    search: str = Query(""),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("DESC")
):
    return await get_paginated_logs(page, per_page, search, sort_by, sort_order)


@router.post("/api/keys")
async def add_key_endpoint(payload: dict = Body(...), user: None = Depends(require_auth)):
    key = payload.get("key", "").strip()
    key_type = payload.get("type", "auto")

    if key_type == "auto" and key.startswith("sk-nry-"):
        key_type = "nry"
    elif key_type == "auto" and key.startswith("dahl_"):
        key_type = "dahl"
    elif key_type == "auto" and key.startswith("wzr_"):
        key_type = "weize"
    elif key_type == "auto" and key.startswith("sk-"):
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(f"{CAVOTI_BASE_URL}/models", headers={"Authorization": f"Bearer {key}"}, timeout=3.0)
                if r.status_code == 200:
                    key_type = "cv"
            except Exception:
                pass

            if key_type == "auto":
                try:
                    r = await client.get(f"{BLUESMINDS_BASE_URL}/models", headers={"Authorization": f"Bearer {key}"}, timeout=3.0)
                    if r.status_code == 200:
                        key_type = "bm"
                except Exception:
                    pass

            if key_type == "auto":
                try:
                    r = await client.get(f"{DAHL_BASE_URL}/models", headers={"Authorization": f"Bearer {key}"}, timeout=3.0)
                    if r.status_code == 200:
                        key_type = "dahl"
                except Exception:
                    pass

            if key_type == "auto":
                try:
                    r = await client.get(f"{QWEN_CLOUD_BASE_URL}/models", headers={"Authorization": f"Bearer {key}"}, timeout=3.0)
                    if r.status_code == 200:
                        key_type = "qc"
                except Exception:
                    pass

    if key_type == "auto" and key.startswith("atms_"):
        key_type = "atomesus"

    success, msg = add_api_key(key, key_type)
    if success:
        await sse_broadcaster.broadcast("status", await _build_status_dict())
    return {"success": success, "message": msg}


@router.delete("/api/keys")
async def remove_key_endpoint(payload: dict = Body(...), user: None = Depends(require_auth)):
    prefix = payload.get("key_prefix", "")
    success, msg = remove_api_key(prefix)
    if success:
        await sse_broadcaster.broadcast("status", await _build_status_dict())
    return {"success": success, "message": msg}


@router.post("/api/keys/reset")
async def reset_key_endpoint(payload: dict = Body(...), user: None = Depends(require_auth)):
    prefix = payload.get("key_prefix", "")
    success, msg = reset_key_status(prefix)
    if success:
        await sse_broadcaster.broadcast("status", await _build_status_dict())
    return {"success": success, "message": msg}


@router.post("/api/keys/set_active")
async def api_set_active_key(payload: dict = Body(...), user: None = Depends(require_auth)):
    prefix = payload.get("key_prefix", "")
    provider = payload.get("provider", None)
    success, msg = set_active_key(prefix, provider)
    if success:
        await sse_broadcaster.broadcast("status", await _build_status_dict())
    return {"success": success, "message": msg}


@router.get("/api/models")
async def api_get_models(user: None = Depends(require_auth)):
    return {
        "kimchi": [f"kc/{m}" for m in config_module.KIMCHI_MODELS],
        "cavoti": [f"cv/{m}" for m in config_module.CAVOTI_MODELS],
        "bluesminds": [f"bm/{m}" for m in config_module.BLUESMINDS_MODELS],
        "bynara": [f"nry/{m}" for m in config_module.NARA_MODELS],
        "dahl": [f"dh/{m}" for m in config_module.DAHL_MODELS_SHORT],
        "qwen_cloud": [f"qc/{m}" for m in config_module.QWEN_CLOUD_MODELS],
        "marketku": [f"mk/{m}" for m in config_module.MARKETKU_MODELS],
        "atomesus": [f"at/{m}" for m in config_module.ATOMESUS_MODELS],
        "weize": config_module.WEIZE_MODELS,
    }


@router.post("/api/models/refresh")
async def api_refresh_models(user: None = Depends(require_auth)):
    """Query all provider APIs and update model lists in .env file."""
    from pathlib import Path

    updated_count = 0
    updates = {}
    errors = {}

    # Provider to base_url mapping
    provider_base_urls = {
        "kc": KIMCHI_BASE_URL,
        "cv": CAVOTI_BASE_URL,
        "bm": BLUESMINDS_BASE_URL,
        "nry": NARA_BASE_URL,
        "dahl": DAHL_BASE_URL,
        "qc": QWEN_CLOUD_BASE_URL,
        "marketku": MARKETKU_BASE_URL,
        "atomesus": ATOMESUS_BASE_URL,
        "weize": WEIZE_BASE_URL,
    }

    # Provider to env var name mapping
    provider_env_vars = {
        "kc": "KIMCHI_MODELS",
        "cv": "CAVOTI_MODELS",
        "bm": "BLUESMINDS_MODELS",
        "nry": "NARA_MODELS",
        "dahl": "DAHL_MODELS",
        "qc": "QWEN_CLOUD_MODELS",
        "marketku": "MARKETKU_MODELS",
        "atomesus": "ATOMESUS_MODELS",
        "weize": "WEIZE_MODELS",
    }

    # Provider to routing prefix mapping (used to strip self-namespaced ids
    # some providers return, e.g. marketku returns "mk/auto" instead of "auto")
    provider_prefixes = {
        "kc": "kc",
        "cv": "cv",
        "bm": "bm",
        "nry": "nry",
        "dahl": "dh",
        "qc": "qc",
        "marketku": "mk",
        "atomesus": "at",
        "weize": "wz",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Get distinct providers from api_keys
        providers = await fetch("SELECT DISTINCT provider FROM api_keys ORDER BY provider")

        for provider_row in providers:
            provider = provider_row["provider"]
            base_url = provider_base_urls.get(provider)
            env_var = provider_env_vars.get(provider)

            if not base_url or not env_var:
                errors[provider] = "No base URL or env var configured"
                continue

            try:
                # Get any available key for this provider
                key_row = await fetch_one(
                    "SELECT key_value FROM api_keys WHERE provider = $1 LIMIT 1",
                    provider
                )

                if not key_row:
                    errors[provider] = "No API key found in database"
                    continue

                api_key = key_row["key_value"]

                # Scan /models endpoint
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                r = await client.get(f"{base_url}/models", headers=headers)

                if r.status_code == 200:
                    data = r.json()
                    models = [m["id"] for m in data.get("data", [])]

                    # Some providers return model ids already namespaced with
                    # our own routing prefix (e.g. marketku returns "mk/auto"),
                    # which would double up once we prefix again for display/dispatch.
                    prefix = f"{provider_prefixes.get(provider, provider)}/"
                    models = [m[len(prefix):] if m.startswith(prefix) else m for m in models]

                    # Deduplicate
                    models = list(dict.fromkeys(models))

                    if models:
                        updates[env_var] = ",".join(models)
                        updated_count += 1
                        print(f"[REFRESH] {provider}: Found {len(models)} models")
                    else:
                        errors[provider] = "No models returned from API"
                else:
                    errors[provider] = f"HTTP {r.status_code}: {r.text[:200]}"
                    print(f"[REFRESH] {provider}: HTTP {r.status_code} - {r.text[:500]}")

            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)}"
                errors[provider] = error_msg
                print(f"[REFRESH] {provider}: {error_msg}")

    # Update .env file
    if updates:
        env_path = Path(".env")
        if env_path.exists():
            lines = env_path.read_text().splitlines()
            new_lines = []
            updated_vars = set()

            # Update existing lines
            for line in lines:
                updated = False
                for key, value in updates.items():
                    if line.startswith(f"{key}="):
                        new_lines.append(f"{key}={value}")
                        updated_vars.add(key)
                        updated = True
                        break
                if not updated:
                    new_lines.append(line)

            # Append new vars that weren't in the file
            for key, value in updates.items():
                if key not in updated_vars:
                    new_lines.append(f"{key}={value}")

            env_path.write_text("\n".join(new_lines) + "\n")
            print(f"[REFRESH] Updated .env with {len(updates)} provider model lists")

        # Reload models in config so changes take effect immediately
        from app import config
        config.reload_models_from_env()

    return {
        "success": True,
        "updated": updated_count,
        "providers": list(updates.keys()),
        "errors": errors,
        "details": {k: len(v.split(",")) for k, v in updates.items()}
    }


def _json_safe_row(row):
    data = dict(row)
    for key, value in data.items():
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
    return data


@router.get("/api/brain/monitor")
async def api_brain_monitor(user: None = Depends(require_auth)):
    # Run all queries in parallel for faster response
    import asyncio

    counts_task = fetch("""
        SELECT
            (SELECT COUNT(*) FROM brain_conversations) AS conversations,
            (SELECT COUNT(*) FROM brain_decisions) AS decisions,
            (SELECT COUNT(*) FROM brain_facts) AS facts,
            (SELECT COUNT(*) FROM brain_profiles) AS profiles,
            (SELECT COUNT(DISTINCT api_key_hash) FROM brain_conversations) AS users,
            (SELECT COUNT(DISTINCT session_id) FROM brain_conversations) AS sessions,
            (SELECT COUNT(*) FROM brain_conversations WHERE embedding IS NOT NULL) AS embedded_messages,
            (SELECT COUNT(*) FROM brain_conversations WHERE created_at >= NOW() - INTERVAL '24 hours') AS messages_24h
    """)

    recent_conversations_task = fetch("""
        SELECT
            bc.id,
            bc.session_id,
            LEFT(bc.api_key_hash, 10) AS user_hash,
            bc.role,
            bc.model,
            LEFT(bc.content, 180) AS content_preview,
            bc.content,
            bc.embedding IS NOT NULL AS has_embedding,
            bc.created_at,
            cs.project_identifier,
            cs.name AS session_name
        FROM brain_conversations bc
        LEFT JOIN chat_sessions cs ON cs.id = bc.session_id
        ORDER BY bc.created_at DESC
        LIMIT 20
    """)

    top_sessions_task = fetch("""
        SELECT
            cs.id,
            cs.name,
            cs.project_identifier,
            cs.last_model,
            LEFT(cs.api_key_hash, 10) AS user_hash,
            COUNT(bc.id) AS brain_messages,
            MAX(bc.created_at) AS last_memory_at
        FROM chat_sessions cs
        JOIN brain_conversations bc ON bc.session_id = cs.id
        GROUP BY cs.id, cs.name, cs.project_identifier, cs.last_model, cs.api_key_hash
        ORDER BY last_memory_at DESC
        LIMIT 10
    """)

    recent_decisions_task = fetch("""
        SELECT
            id,
            session_id,
            LEFT(api_key_hash, 10) AS user_hash,
            decision_type,
            title,
            LEFT(COALESCE(outcome, description, context, ''), 180) AS detail_preview,
            created_at
        FROM brain_decisions
        ORDER BY created_at DESC
        LIMIT 10
    """)

    recent_facts_task = fetch("""
        SELECT
            id,
            session_id,
            LEFT(api_key_hash, 10) AS user_hash,
            category,
            fact,
            confidence,
            created_at
        FROM brain_facts
        ORDER BY created_at DESC
        LIMIT 10
    """)

    # Wait for all queries to complete in parallel
    counts, recent_conversations, top_sessions, recent_decisions, recent_facts = await asyncio.gather(
        counts_task,
        recent_conversations_task,
        top_sessions_task,
        recent_decisions_task,
        recent_facts_task
    )

    return {
        "counts": _json_safe_row(counts[0]) if counts else {},
        "recent_conversations": [_json_safe_row(row) for row in recent_conversations],
        "top_sessions": [_json_safe_row(row) for row in top_sessions],
        "recent_decisions": [_json_safe_row(row) for row in recent_decisions],
        "recent_facts": [_json_safe_row(row) for row in recent_facts],
    }


@router.get("/api/brain/session/{session_id}/messages")
async def api_brain_session_messages(session_id: int, user: None = Depends(require_auth)):
    messages = await fetch("""
        SELECT
            bc.id,
            bc.role,
            bc.model,
            LEFT(bc.content, 180) AS content_preview,
            bc.content,
            bc.embedding IS NOT NULL AS has_embedding,
            bc.created_at
        FROM brain_conversations bc
        WHERE bc.session_id = $1
        ORDER BY bc.created_at ASC
    """, session_id)

    return {
        "session_id": session_id,
        "messages": [_json_safe_row(row) for row in messages],
        "count": len(messages)
    }


@router.get("/api/sse")
async def sse_endpoint(request: Request, user: None = Depends(require_auth)):
    async def event_generator() -> AsyncGenerator[str, None]:
        q = sse_broadcaster.connect()
        try:
            status = await _build_status_dict()
            yield f"data: {json.dumps({'type': 'status', 'payload': status})}\n\n"
            while True:
                try:
                    data = await __import__('asyncio').wait_for(q.get(), timeout=15.0)
                    yield f"data: {data}\n\n"
                except __import__('asyncio').TimeoutError:
                    yield ":keepalive\n\n"
        finally:
            sse_broadcaster.disconnect(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/api/router-keys")
async def api_get_router_keys(user: None = Depends(require_auth)):
    """Get all router API keys."""
    keys = await get_router_api_keys()
    return {"keys": [_json_safe_row(k) for k in keys]}


@router.post("/api/router-keys")
async def api_create_router_key(payload: dict = Body(...), user: None = Depends(require_auth)):
    """Generate a new router API key."""
    key_name = payload.get("key_name", "").strip()
    if not key_name:
        return JSONResponse(status_code=400, content={"success": False, "message": "Key name is required"})

    key = await create_router_api_key(key_name)
    return {"success": True, "key": _json_safe_row(key)}


@router.delete("/api/router-keys/{key_id}")
async def api_delete_router_key(key_id: int, user: None = Depends(require_auth)):
    """Delete a router API key."""
    await delete_router_api_key(key_id)
    return {"success": True, "message": "Key deleted"}
