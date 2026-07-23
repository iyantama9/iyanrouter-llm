import json
import time
from typing import AsyncGenerator

from fastapi import APIRouter, Request, Body, Cookie, Depends, Query, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import httpx

import app.config as config_module
from app.config import (
    CAVOTI_BASE_URL, BLUESMINDS_BASE_URL, DAHL_BASE_URL, QWEN_CLOUD_BASE_URL, ROUTER_PASSWORD,
    KIMCHI_MODELS, CAVOTI_MODELS, BLUESMINDS_MODELS, NARA_MODELS, DAHL_MODELS_SHORT, QWEN_CLOUD_MODELS, MARKETKU_MODELS, ATOMESUS_MODELS, WEIZE_MODELS,
    recent_requests,
    add_api_key, remove_api_key, reset_key_status, get_masked_keys, set_active_key,
    SESSION_SECRET, ADMIN_USERNAME, verify_admin_password, get_paginated_logs,
)
from app.sse import sse_broadcaster


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
        "kimchi": [f"kc/{m}" for m in KIMCHI_MODELS],
        "cavoti": [f"cv/{m}" for m in CAVOTI_MODELS],
        "bluesminds": [f"bm/{m}" for m in BLUESMINDS_MODELS],
        "bynara": [f"nry/{m}" for m in NARA_MODELS],
        "dahl": [f"dh/{m}" for m in DAHL_MODELS_SHORT],
        "qwen_cloud": [f"qc/{m}" for m in QWEN_CLOUD_MODELS],
        "marketku": [f"mk/{m}" for m in MARKETKU_MODELS],
        "atomesus": [f"at/{m}" for m in ATOMESUS_MODELS],
        "weize": WEIZE_MODELS,
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
