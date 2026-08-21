import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

import app.config as config_module
from app.config import (
    DEFAULT_UPSTREAM_URL, CAVOTI_API_KEY, CAVOTI_BASE_URL, BLUESMINDS_API_KEY, BLUESMINDS_BASE_URL,
    ROUTER_PASSWORD, get_current_key, rotate_key, API_KEYS, NARA_BASE_URL, DAHL_BASE_URL, QWEN_CLOUD_BASE_URL, MARKETKU_BASE_URL, ATOMESUS_BASE_URL, WEIZE_BASE_URL,
    resolve_dahl_model, CV_API_KEYS, BM_API_KEYS, NR_API_KEYS, DAHL_API_KEYS, QC_API_KEYS, MARKETKU_API_KEYS, ATOMESUS_API_KEYS, WEIZE_API_KEYS,
    get_current_cv_key, rotate_cv_key, get_current_bm_key, rotate_bm_key, get_current_nr_key, rotate_nr_key, get_current_dahl_key, rotate_dahl_key, get_current_qc_key, rotate_qc_key, get_current_marketku_key, rotate_marketku_key, get_current_atomesus_key, rotate_atomesus_key, get_current_weize_key, rotate_weize_key,
    get_current_qc_key_for_model, rotate_qc_key_for_model, mark_qc_model_exhausted, QC_FALLBACK_ORDER,
    recent_requests, add_request_log,
)
from app.translator import (
    build_openai_request, to_anthropic_response, stream_as_anthropic, compact_messages,
    is_context_window_error, to_anthropic_stream_error, estimate_tokens,
)
from app.translator_openai import (
    openai_to_anthropic_messages, anthropic_to_openai_response, anthropic_to_openai_stream_chunk
)
from app.sse import sse_broadcaster
from app.brain.middleware import BrainMiddleware
from app.brain.memory import MemoryManager
from app.brain.storage import BrainStorage
from app.database import verify_router_api_key


router = APIRouter()


async def _build_status_dict():
    from app.config import get_masked_keys
    uptime_seconds = int(time.time() - config_module.START_TIME)
    _all_keys = get_masked_keys()
    available_keys = sum(1 for k in _all_keys if k['status'] in ('Active', 'Standby'))
    return {
        "status": "online",
        "uptime_seconds": uptime_seconds,
        "total_requests": config_module.total_requests,
        "failover_count": config_module.failover_count,
        "total_tokens": config_module.total_tokens,
        "available_keys": available_keys,
        "total_keys": len(_all_keys),
        "keys": _all_keys,
        "recent_requests": recent_requests
    }


async def _check_router_auth(request: Request):
    """Check router authentication via password or API key."""
    auth_header = request.headers.get("Authorization")
    x_api_key = request.headers.get("x-api-key")

    # Extract token from header
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
    elif x_api_key:
        token = x_api_key

    if not token:
        return False

    # Check ROUTER_PASSWORD first (backward compatibility)
    if ROUTER_PASSWORD and token == ROUTER_PASSWORD:
        return True

    # Check router API keys from database
    if token.startswith("rtr_"):
        return await verify_router_api_key(token)

    return False


async def _save_brain_exchange(
    session_id: int,
    api_key_hash: str,
    user_content: str,
    assistant_content,
    model: str,
):
    """Persist a completed exchange and index both messages in the brain."""
    from app.database import append_to_session

    user_row = await append_to_session(session_id, "user", user_content)
    await BrainMiddleware.save_conversation_to_brain(
        session_id=session_id,
        api_key_hash=api_key_hash,
        message_id=user_row["id"],
        role="user",
        content=user_content,
        model=model,
    )

    if not assistant_content:
        return

    stored_assistant = (
        assistant_content
        if isinstance(assistant_content, str)
        else json.dumps(assistant_content)
    )
    assistant_text = MemoryManager._extract_text_from_content(stored_assistant)
    if not assistant_text.strip():
        return

    assistant_row = await append_to_session(
        session_id, "assistant", stored_assistant
    )
    await BrainMiddleware.save_conversation_to_brain(
        session_id=session_id,
        api_key_hash=api_key_hash,
        message_id=assistant_row["id"],
        role="assistant",
        content=assistant_text,
        model=model,
    )


async def _dispatch_custom_provider(prefix: str, payload: dict, stream: bool):
    """
    Send an Anthropic-shaped request to an admin-added custom provider and
    return an Anthropic-shaped result, regardless of whether that provider
    speaks OpenAI or Anthropic upstream. Callers on the OpenAI-compatible
    endpoint convert their request to this shape first and the response back
    afterwards -- this function only ever deals in Anthropic shape.

    Returns either:
      ("json", status_code, dict)                          for non-streaming
      ("stream", status_code, async_generator[str] | None)  for streaming
    A None generator means the caller should fall back to the status/dict
    error path instead (used when we fail before ever reaching upstream).
    """
    info = config_module.CUSTOM_PROVIDERS.get(prefix)
    if not info:
        return "json", 400, {"error": {"message": f"Unknown provider '{prefix}'"}}

    keys = config_module.CUSTOM_PROVIDER_KEYS.get(prefix) or []
    if not keys:
        return "json", 500, {"error": {"message": f"No API keys configured for provider '{prefix}'"}}

    base_url = info["base_url"]
    api_format = info["api_format"]
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    model = payload.get("model", "")

    last_status, last_body = 502, {"error": {"message": "All configured keys for this provider failed."}}

    for attempt in range(len(keys)):
        key = config_module.get_current_custom_key(prefix)
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        try:
            if api_format == "anthropic":
                url = f"{base_url}/messages"
                upstream_payload = dict(payload)

                if not stream:
                    async with httpx.AsyncClient(timeout=300) as client:
                        resp = await client.post(url, headers=headers, json=upstream_payload)
                    if resp.status_code == 200:
                        return "json", 200, resp.json()
                    try:
                        body = resp.json()
                    except Exception:
                        body = {"error": {"message": resp.text}}
                    last_status, last_body = resp.status_code, body
                else:
                    client = httpx.AsyncClient(timeout=300)
                    req = client.build_request("POST", url, headers=headers, json=upstream_payload)
                    resp = await client.send(req, stream=True)
                    if resp.status_code == 200:
                        async def _relay():
                            try:
                                async for chunk in resp.aiter_bytes():
                                    yield chunk
                            finally:
                                await resp.aclose()
                                await client.aclose()
                        return "stream", 200, _relay()
                    body_bytes = await resp.aread()
                    await resp.aclose()
                    await client.aclose()
                    try:
                        last_body = json.loads(body_bytes)
                    except Exception:
                        last_body = {"error": {"message": body_bytes.decode(errors="replace")}}
                    last_status = resp.status_code

            else:  # openai-compatible upstream
                url = f"{base_url}/chat/completions"
                upstream_payload = build_openai_request(payload, provider=prefix)
                upstream_payload["stream"] = stream

                if not stream:
                    async with httpx.AsyncClient(timeout=300) as client:
                        resp = await client.post(url, headers=headers, json=upstream_payload)
                    if resp.status_code == 200:
                        anthropic_resp = to_anthropic_response(resp.json(), model, msg_id)
                        return "json", 200, anthropic_resp
                    try:
                        body = resp.json()
                    except Exception:
                        body = {"error": {"message": resp.text}}
                    last_status, last_body = resp.status_code, body
                else:
                    client = httpx.AsyncClient(timeout=300)
                    req = client.build_request("POST", url, headers=headers, json=upstream_payload)
                    resp = await client.send(req, stream=True)
                    if resp.status_code == 200:
                        async def _relay():
                            try:
                                async for chunk in stream_as_anthropic(resp, model, msg_id):
                                    yield chunk
                            finally:
                                await resp.aclose()
                                await client.aclose()
                        return "stream", 200, _relay()
                    body_bytes = await resp.aread()
                    await resp.aclose()
                    await client.aclose()
                    try:
                        last_body = json.loads(body_bytes)
                    except Exception:
                        last_body = {"error": {"message": body_bytes.decode(errors="replace")}}
                    last_status = resp.status_code

        except Exception as e:
            last_status, last_body = 502, {"error": {"message": f"{type(e).__name__}: {e}"}}

        if attempt < len(keys) - 1:
            config_module.rotate_custom_key(prefix)
        else:
            break

    return "json", last_status, last_body


def _qwen_image_request(payload: dict) -> dict:
    prompt = ""
    image_urls = []

    for message in reversed(payload.get("messages", [])):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            prompt = content
        elif isinstance(content, list):
            text_parts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("text"):
                    text_parts.append(block["text"])
                elif block.get("type") == "text" and block.get("text"):
                    text_parts.append(block["text"])
                elif block.get("type") == "image" and isinstance(block.get("source"), dict):
                    source = block["source"]
                    if source.get("type") == "url" and source.get("url"):
                        image_urls.append(source["url"])
                    elif source.get("type") == "base64" and source.get("data"):
                        media_type = source.get("media_type", "image/png")
                        image_urls.append(f"data:{media_type};base64,{source['data']}")
                elif block.get("type") == "image_url":
                    url = block.get("image_url", {}).get("url")
                    if url:
                        image_urls.append(url)
                elif block.get("image"):
                    image_urls.append(block["image"])
            prompt = "\n".join(text_parts)
        break

    content = [{"image": url} for url in image_urls]
    content.append({"text": prompt or ("Edit this image" if image_urls else "Generate an image")})

    return {
        "model": payload["model"],
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ]
        },
        "parameters": {
            "prompt_extend": True,
            "watermark": False,
            "size": "1024*1024",
            "n": 1,
        },
    }


def _qwen_image_response(data: dict, model: str, msg_id: str) -> dict:
    choices = data.get("output", {}).get("choices", [])
    blocks = choices[0].get("message", {}).get("content", []) if choices else []
    content = [
        {"type": "image", "source": {"type": "url", "url": block["image"]}}
        for block in blocks
        if isinstance(block, dict) and block.get("image")
    ]
    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
        },
    }


@router.get("/v1/models")
@router.get("/models")
async def list_models(request: Request):
    if not await _check_router_auth(request):
        return JSONResponse(status_code=401, content={"error": {"message": "Invalid router password."}})

    models = []
    disabled = config_module.DISABLED_PROVIDERS
    if "kc" not in disabled:
        for m in config_module.KIMCHI_MODELS:
            models.append(f"kc/{m}")
    if "cv" not in disabled:
        for m in config_module.CAVOTI_MODELS:
            models.append(f"cv/{m}")
    if "bm" not in disabled:
        for m in config_module.BLUESMINDS_MODELS:
            models.append(f"bm/{m}")
    if "nry" not in disabled:
        for m in config_module.NARA_MODELS:
            models.append(f"nry/{m}")
    if "dahl" not in disabled:
        for m in config_module.DAHL_MODELS:
            models.append(f"dh/{m}")
    if "qc" not in disabled:
        for m in config_module.QWEN_CLOUD_MODELS:
            models.append(f"qc/{m}")
    if "marketku" not in disabled:
        for m in config_module.MARKETKU_MODELS:
            models.append(f"mk/{m}")
    if "atomesus" not in disabled:
        for m in config_module.ATOMESUS_MODELS:
            models.append(f"at/{m}")
    if "weize" not in disabled:
        for m in config_module.WEIZE_MODELS:
            models.append(f"wz/{m}")
    for prefix, info in config_module.CUSTOM_PROVIDERS.items():
        for m in info.get("models") or []:
            models.append(f"{prefix}/{m}")

    data = []
    for m in models:
        data.append({
            "id": m,
            "object": "model",
            "created": 1700000000,
            "owned_by": "iyan-router"
        })

    return JSONResponse(content={"object": "list", "data": data})


@router.post("/v1/messages/count_tokens")
async def count_tokens(body: dict = Body(...)):
    tokens = estimate_tokens(body)
    return {"input_tokens": tokens}


@router.post("/v1/messages")
async def messages(request: Request):
    if not await _check_router_auth(request):
        return JSONResponse(status_code=401, content={"error": {"message": "Invalid router password."}})

    payload = await request.json()

    # Custom (admin-added) providers get a self-contained dispatch path,
    # short-circuiting before any of the built-in routing/brain logic below.
    requested_model_raw = payload.get("model", "") or ""
    for cprefix in config_module.CUSTOM_PROVIDERS:
        if requested_model_raw.startswith(f"{cprefix}/"):
            payload["model"] = requested_model_raw[len(cprefix) + 1:]
            want_stream = bool(payload.get("stream"))
            start_req_time = time.time()
            kind, status, body = await _dispatch_custom_provider(cprefix, payload, want_stream)
            log_model = f"{cprefix}/{payload['model']}"
            if kind == "stream":
                async def _wrapped():
                    async for chunk in body:
                        yield chunk
                add_request_log(log_model, status, "custom", False, int((time.time() - start_req_time) * 1000))
                return StreamingResponse(_wrapped(), media_type="text/event-stream")
            add_request_log(log_model, status, "custom", False, int((time.time() - start_req_time) * 1000))
            return JSONResponse(status_code=status, content=body)

    # Brain: Always enabled for all users
    enable_brain = True

    # Conversation Memory: Extract session headers
    enable_memory = request.headers.get("X-Enable-Memory", "false").lower() == "true"
    session_id_header = request.headers.get("X-Session-Id")

    # Brain: Extract user message for context building
    user_message_text = ""
    last_user_msg = None
    if enable_brain:
        messages = payload.get("messages", [])
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_msg = msg
                content = msg.get("content", "")
                if isinstance(content, str):
                    user_message_text = content
                elif isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                    user_message_text = "\n".join(text_parts)
                break

    provider = "kc"
    if payload.get("model"):
        if payload["model"].startswith("cv/") or payload["model"] in config_module.CAVOTI_MODELS:
            provider = "cv"
        elif payload["model"].startswith("bm/") or payload["model"] in config_module.BLUESMINDS_MODELS:
            provider = "bm"
        elif payload["model"].startswith("nry/") or payload["model"] in config_module.NARA_MODELS:
            provider = "nry"
        elif payload["model"].startswith("dh/") or payload["model"] in config_module.DAHL_MODELS_SHORT:
            provider = "dahl"
        elif payload["model"].startswith("qc/"):
            provider = "qc"
        elif payload["model"].startswith("mk/") or payload["model"] in config_module.MARKETKU_MODELS:
            provider = "marketku"
        elif payload["model"].startswith("at/") or payload["model"] in config_module.ATOMESUS_MODELS:
            provider = "atomesus"
        elif payload["model"].startswith("wz/") or payload["model"] in config_module.WEIZE_MODELS:
            provider = "weize"
        elif payload["model"].startswith("kc/") or payload["model"] in config_module.KIMCHI_MODELS:
            provider = "kc"

    if provider in config_module.DISABLED_PROVIDERS:
        return JSONResponse(status_code=503, content={"error": {"message": f"Provider '{provider}' has been removed."}})

    for prefix in ("kc/", "cv/", "bm/", "nry/", "dh/", "qc/", "mk/", "at/", "wz/"):
        if payload.get("model", "").startswith(prefix):
            payload["model"] = payload["model"][len(prefix):]
            break

    if provider == "dahl":
        payload["model"] = resolve_dahl_model(payload["model"])

    # Determine current API key for both memory and brain
    current_key = ""
    if provider == "cv":
        current_key = get_current_cv_key() if CV_API_KEYS else CAVOTI_API_KEY or ""
    elif provider == "bm":
        current_key = get_current_bm_key() if BM_API_KEYS else BLUESMINDS_API_KEY or ""
    elif provider == "nry":
        current_key = get_current_nr_key() if NR_API_KEYS else ""
    elif provider == "dahl":
        current_key = get_current_dahl_key() if DAHL_API_KEYS else ""
    elif provider == "qc":
        current_key = get_current_qc_key() if QC_API_KEYS else ""
    elif provider == "marketku":
        current_key = get_current_marketku_key() if MARKETKU_API_KEYS else ""
    elif provider == "atomesus":
        current_key = get_current_atomesus_key() if ATOMESUS_API_KEYS else ""
    elif provider == "weize":
        current_key = get_current_weize_key() if WEIZE_API_KEYS else ""
    else:
        current_key = get_current_key() if API_KEYS else ""

    # Brain sessions are automatic. Explicit session IDs let clients preserve
    # continuity; otherwise the stable first user message identifies a thread.
    auth_header = request.headers.get("Authorization", "")
    x_api_key = request.headers.get("x-api-key", "")
    client_credential = auth_header[7:] if auth_header.startswith("Bearer ") else x_api_key
    api_key_hash_for_brain = BrainMiddleware.get_api_key_hash(client_credential)

    if not session_id_header:
        first_user = next((m for m in payload.get("messages", []) if m.get("role") == "user"), None)
        first_user_text = MemoryManager._extract_text_from_content(
            json.dumps(first_user.get("content", "")) if first_user else ""
        )
        # Normalize whitespace so trivial formatting differences (trailing
        # newline, double spaces) don't fragment the same thread into two
        # "sessions". Clients that want real continuity should still send
        # X-Session-Id explicitly — this is a best-effort fallback only.
        normalized_first_text = " ".join(first_user_text.split())
        session_id_header = BrainMiddleware.get_api_key_hash(
            f"{api_key_hash_for_brain}:{normalized_first_text}"
        )[:16]

    from app.database import get_or_create_session, load_session_history
    session_id = await get_or_create_session(
        identifier=session_id_header,
        api_key_hash=api_key_hash_for_brain,
        model=payload.get("model")
    )

    session_history = None
    if enable_memory:
        history_rows = await load_session_history(session_id, limit=config_module.MAX_HISTORY_MESSAGES)
        if history_rows:
            session_history = []
            for row in history_rows:
                content_str = row["content"]
                try:
                    content = json.loads(content_str)
                    if isinstance(content, list):
                        text_parts = [
                            block.get("text", "")
                            for block in content
                            if isinstance(block, dict) and block.get("type") == "text"
                        ]
                        content_str = "\n".join(text_parts) if text_parts else content_str
                except Exception:
                    pass
                session_history.append({"role": row["role"], "content": content_str})

    # Brain: Build context from brain memory
    brain_context = None
    if enable_brain and user_message_text:
        brain_context = await BrainMiddleware.build_brain_context(
            api_key_hash=api_key_hash_for_brain,
            user_message=user_message_text,
            session_id=session_id,
            enable_brain=enable_brain
        )

    # Brain: models this user has recently given explicit negative feedback
    # about, via DecisionTracker.apply_outcome_feedback. Used below to
    # deprioritize (never hard-exclude) candidates when QC does automatic
    # model fallback, so routing actually learns from past outcomes instead
    # of only ever producing text for the prompt. Fails open on any error.
    avoided_qc_models = set()
    if enable_brain:
        try:
            avoided_qc_models = await BrainStorage.get_avoided_models(api_key_hash_for_brain)
        except Exception as e:
            print(f"[BRAIN] Error loading avoided models: {e}")

    # Brain: Inject brain context into payload
    if brain_context:
        payload = await BrainMiddleware.inject_brain_context(payload, brain_context)

    if provider == "cv":
        upstream_base_url = CAVOTI_BASE_URL
        log_model = f"cv/{payload['model']}"
    elif provider == "bm":
        upstream_base_url = BLUESMINDS_BASE_URL
        log_model = f"bm/{payload['model']}"
    elif provider == "nry":
        upstream_base_url = NARA_BASE_URL
        log_model = f"nry/{payload['model']}"
    elif provider == "dahl":
        upstream_base_url = DAHL_BASE_URL
        log_model = f"dh/{payload['model'].split('/', 1)[-1]}"
    elif provider == "qc":
        upstream_base_url = QWEN_CLOUD_BASE_URL
        log_model = f"qc/{payload['model']}"
    elif provider == "marketku":
        upstream_base_url = MARKETKU_BASE_URL
        log_model = f"mk/{payload['model']}"
    elif provider == "atomesus":
        upstream_base_url = ATOMESUS_BASE_URL
        log_model = f"at/{payload['model']}"
    elif provider == "weize":
        upstream_base_url = WEIZE_BASE_URL
        log_model = payload['model']
    else:
        upstream_base_url = DEFAULT_UPSTREAM_URL
        log_model = f"kc/{payload['model']}"

    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    upstream_req = build_openai_request(payload, provider=provider, session_history=session_history)
    upstream_endpoint = f"{upstream_base_url}/chat/completions"

    input_tokens = estimate_tokens(payload)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "kimchi/0.2.0",
    }

    original_messages = upstream_req["messages"][:]
    compact_levels = [None, 20, 6]

    if provider == "cv":
        api_keys_to_use = CV_API_KEYS
    elif provider == "bm":
        api_keys_to_use = BM_API_KEYS
    elif provider == "nry":
        api_keys_to_use = NR_API_KEYS
    elif provider == "dahl":
        api_keys_to_use = DAHL_API_KEYS
    elif provider == "qc":
        api_keys_to_use = QC_API_KEYS
    elif provider == "marketku":
        api_keys_to_use = MARKETKU_API_KEYS
    elif provider == "atomesus":
        api_keys_to_use = ATOMESUS_API_KEYS
    elif provider == "weize":
        api_keys_to_use = WEIZE_API_KEYS
    else:
        api_keys_to_use = API_KEYS

    if not api_keys_to_use:
        if provider == "cv" and CAVOTI_API_KEY:
            api_keys_to_use = [CAVOTI_API_KEY]
        elif provider == "bm" and BLUESMINDS_API_KEY:
            api_keys_to_use = [BLUESMINDS_API_KEY]
        else:
            return JSONResponse(status_code=500, content={"error": "No upstream API keys available"})

    requested_qc_model = payload.get("model") if provider == "qc" else None
    is_qwen_image = provider == "qc" and "image" in payload.get("model", "").lower()

    if is_qwen_image:
        image_endpoint = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        image_request = _qwen_image_request(payload)
        last_status = 500
        last_error = {"error": {"message": "Image generation failed"}}

        for _ in range(len(QC_API_KEYS)):
            current_key = get_current_qc_key_for_model(requested_qc_model)
            start_req_time = time.time()
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(
                    image_endpoint,
                    headers={
                        "Authorization": f"Bearer {current_key}",
                        "Content-Type": "application/json",
                    },
                    json=image_request,
                )

            try:
                data = resp.json()
            except Exception:
                data = {"error": {"message": resp.text or f"HTTP {resp.status_code}"}}

            if resp.status_code == 200:
                result = _qwen_image_response(data, log_model, msg_id)
                total_ms = int((time.time() - start_req_time) * 1000)
                add_request_log(log_model, 200, current_key, False, total_ms, input_tokens, 0)
                await sse_broadcaster.broadcast("log", recent_requests[0] if recent_requests else {})
                await sse_broadcaster.broadcast("status", await _build_status_dict())
                return JSONResponse(result)

            last_status = resp.status_code
            message = data.get("message") or data.get("error", {}).get("message") or f"HTTP {resp.status_code}"
            last_error = {"error": {"message": message}}
            add_request_log(
                log_model,
                resp.status_code,
                current_key,
                True,
                int((time.time() - start_req_time) * 1000),
            )
            if resp.status_code in (401, 402, 403, 429) and rotate_qc_key_for_model(requested_qc_model):
                continue
            break

        return JSONResponse(status_code=last_status, content=last_error)

    if upstream_req.get("stream"):
        async def generate():
            nonlocal requested_qc_model
            nonlocal log_model
            nonlocal session_id
            nonlocal enable_memory
            last_error_status = 429
            last_error_content = {"error": {"message": "All configured API keys are rate limited or unauthorized."}}

            # Conversation Memory: Accumulator for streaming response
            accumulated_response = []

            for c_idx, compact_level in enumerate(compact_levels):
                if compact_level is not None:
                    upstream_req["messages"] = compact_messages(original_messages, keep_last=compact_level)
                    print(f"[LOG] Auto-compacting context → keeping last {compact_level} messages ({len(upstream_req['messages'])} total)")

                context_window_hit = False
                rotated_occurred = False
                model_switched = False

                for attempt in range(len(api_keys_to_use)):
                    if provider == "cv":
                        current_key = get_current_cv_key()
                    elif provider == "bm":
                        current_key = get_current_bm_key()
                    elif provider == "nry":
                        current_key = get_current_nr_key()
                    elif provider == "dahl":
                        current_key = get_current_dahl_key()
                    elif provider == "qc":
                        current_key = get_current_qc_key_for_model(requested_qc_model)
                    elif provider == "marketku":
                        current_key = get_current_marketku_key()
                    elif provider == "atomesus":
                        current_key = get_current_atomesus_key()
                    elif provider == "weize":
                        current_key = get_current_weize_key()
                    else:
                        current_key = get_current_key()

                    headers["Authorization"] = f"Bearer {current_key}"

                    start_req_time = time.time()
                    async with httpx.AsyncClient(timeout=300) as client:
                        try:
                            has_yielded = False
                            first_token_time = None
                            async with client.stream(
                                "POST",
                                upstream_endpoint,
                                headers=headers,
                                json=upstream_req,
                            ) as resp:
                                if resp.status_code in (401, 402, 403, 404, 429, 500, 502, 503, 504):
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

                                    if provider == "qc" and requested_qc_model:
                                        mark_qc_model_exhausted(current_key, requested_qc_model)
                                        if rotate_qc_key_for_model(requested_qc_model):
                                            print(f"[LOG] QC model {requested_qc_model} exhausted on key, trying next key for same model")
                                            continue
                                        fallback = None
                                        for m in QC_FALLBACK_ORDER:
                                            if m != requested_qc_model and m in config_module.QWEN_CLOUD_MODELS and m not in avoided_qc_models:
                                                if any(not config_module.is_qc_model_exhausted(k, m) for k in QC_API_KEYS):
                                                    fallback = m
                                                    break
                                        if not fallback:
                                            # Nothing outside the brain's avoid-list is available; fall
                                            # back to it rather than failing the request outright.
                                            for m in QC_FALLBACK_ORDER:
                                                if m != requested_qc_model and m in config_module.QWEN_CLOUD_MODELS:
                                                    if any(not config_module.is_qc_model_exhausted(k, m) for k in QC_API_KEYS):
                                                        fallback = m
                                                        break
                                        if fallback:
                                            print(f"[LOG] All QC keys exhausted for {requested_qc_model}, falling back to {fallback}")
                                            requested_qc_model = fallback
                                            upstream_req["model"] = fallback
                                            log_model = f"qc/{fallback}"
                                            model_switched = True
                                            continue

                                    if provider == "kc":
                                        rotate_key()
                                    elif provider == "cv":
                                        rotate_cv_key()
                                    elif provider == "bm":
                                        rotate_bm_key()
                                    elif provider == "nry":
                                        rotate_nr_key()
                                    elif provider == "dahl":
                                        rotate_dahl_key()
                                    elif provider == "qc":
                                        rotate_qc_key()
                                    elif provider == "marketku":
                                        rotate_marketku_key()
                                    elif provider == "atomesus":
                                        rotate_atomesus_key()
                                    elif provider == "weize":
                                        rotate_weize_key()
                                    last_error_status = resp.status_code
                                    last_error_content = err_data or {"error": f"HTTP {resp.status_code} error"}
                                    await sse_broadcaster.broadcast("status", await _build_status_dict())
                                    continue

                                if resp.status_code != 200:
                                    try:
                                        await resp.aread()
                                        err_data = resp.json()
                                    except Exception:
                                        err_data = {"error": f"HTTP {resp.status_code} error body not readable"}
                                    if resp.status_code == 400 and is_context_window_error(err_data) and c_idx < len(compact_levels) - 1:
                                        print(f"[LOG] Context window exceeded, auto-compacting...")
                                        context_window_hit = True
                                        break
                                    add_request_log(log_model, resp.status_code, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                                    yield f"event: error\ndata: {json.dumps(to_anthropic_stream_error(err_data))}\n\n"
                                    return

                                token_tracker = {"output_tokens": 0}
                                async for chunk in stream_as_anthropic(resp, log_model, msg_id, input_tokens, token_tracker):
                                    has_yielded = True
                                    if first_token_time is None:
                                        first_token_time = time.time()

                                    # Accumulate response content for brain persistence
                                    if enable_brain and session_id:
                                        try:
                                            # Parse SSE chunk to extract content
                                            if chunk.startswith("event: content_block_start\ndata: "):
                                                data_json = chunk.split("\ndata: ", 1)[1].strip()
                                                data = json.loads(data_json)
                                                if data.get("type") == "content_block_start":
                                                    content_block = data.get("content_block", {})
                                                    idx = data.get("index", len(accumulated_response))
                                                    # Ensure list is large enough
                                                    while len(accumulated_response) <= idx:
                                                        accumulated_response.append(None)
                                                    accumulated_response[idx] = content_block.copy()
                                            elif chunk.startswith("event: content_block_delta\ndata: "):
                                                data_json = chunk.split("\ndata: ", 1)[1].strip()
                                                data = json.loads(data_json)
                                                if data.get("type") == "content_block_delta":
                                                    idx = data.get("index", 0)
                                                    delta = data.get("delta", {})
                                                    # Ensure block exists
                                                    while len(accumulated_response) <= idx:
                                                        accumulated_response.append(None)
                                                    if accumulated_response[idx] is None:
                                                        accumulated_response[idx] = {}

                                                    # Append delta content
                                                    if delta.get("type") == "text_delta":
                                                        text = delta.get("text", "")
                                                        if "text" not in accumulated_response[idx]:
                                                            accumulated_response[idx]["type"] = "text"
                                                            accumulated_response[idx]["text"] = ""
                                                        accumulated_response[idx]["text"] += text
                                                    elif delta.get("type") == "thinking_delta":
                                                        thinking = delta.get("thinking", "")
                                                        if "thinking" not in accumulated_response[idx]:
                                                            accumulated_response[idx]["type"] = "thinking"
                                                            accumulated_response[idx]["thinking"] = ""
                                                        accumulated_response[idx]["thinking"] += thinking
                                        except Exception:
                                            pass  # Ignore parsing errors

                                    yield chunk
                                total_ms = int((time.time() - start_req_time) * 1000)
                                ttft_ms = int((first_token_time - start_req_time) * 1000) if first_token_time else total_ms
                                add_request_log(log_model, 200, current_key, rotated_occurred, total_ms, input_tokens, token_tracker["output_tokens"])

                                final_response = [
                                    block for block in accumulated_response if block is not None
                                ]
                                if enable_brain and user_message_text:
                                    await _save_brain_exchange(
                                        session_id=session_id,
                                        api_key_hash=api_key_hash_for_brain,
                                        user_content=user_message_text,
                                        assistant_content=final_response,
                                        model=log_model,
                                    )

                                threshold = config_module.SLOW_RESPONSE_THRESHOLD_MS
                                if threshold > 0 and ttft_ms > threshold and len(api_keys_to_use) > 1:
                                    print(f"[LOG] Slow TTFT {ttft_ms}ms > {threshold}ms, rotating {provider} key proactively")
                                    if provider == "kc":
                                        rotate_key(reason="Slow")
                                    elif provider == "cv":
                                        rotate_cv_key(reason="Slow")
                                    elif provider == "bm":
                                        rotate_bm_key(reason="Slow")
                                    elif provider == "nry":
                                        rotate_nr_key(reason="Slow")
                                    elif provider == "qc":
                                        rotate_qc_key_for_model(requested_qc_model)
                                    elif provider == "marketku":
                                        rotate_marketku_key()
                                await sse_broadcaster.broadcast("log", recent_requests[0] if recent_requests else {})
                                await sse_broadcaster.broadcast("status", await _build_status_dict())
                                return
                        except Exception as e:
                            print(f"[STREAM ERROR] Exception during attempt {attempt} (key: {current_key[:10]}...): {type(e).__name__}: {str(e)}")
                            import traceback; traceback.print_exc()

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
                            elif provider == "nry":
                                rotate_nr_key()
                            elif provider == "dahl":
                                rotate_dahl_key()
                            elif provider == "qc":
                                rotate_qc_key()
                            elif provider == "marketku":
                                rotate_marketku_key()
                            elif provider == "atomesus":
                                rotate_atomesus_key()
                            elif provider == "weize":
                                rotate_weize_key()
                            last_error_status = 500
                            last_error_content = {"error": str(e)}
                            await sse_broadcaster.broadcast("status", await _build_status_dict())

                if model_switched:
                    model_switched = False
                    continue

                if context_window_hit:
                    continue

                yield f"event: error\ndata: {json.dumps(to_anthropic_stream_error(last_error_content))}\n\n"
                return

            yield f"event: error\ndata: {json.dumps(to_anthropic_stream_error('Konteks terlalu panjang bahkan setelah auto-compact. Silakan mulai percakapan baru.'))}\n\n"
            return

        return StreamingResponse(generate(), media_type="text/event-stream")

    last_error_status = 429
    last_error_content = {"error": {"message": "All configured API keys are rate limited or unauthorized."}}

    for c_idx, compact_level in enumerate(compact_levels):
        if compact_level is not None:
            upstream_req["messages"] = compact_messages(original_messages, keep_last=compact_level)
            print(f"[LOG] Auto-compacting context → keeping last {compact_level} messages ({len(upstream_req['messages'])} total)")

        context_window_hit = False
        rotated_occurred = False
        model_switched = False

        for attempt in range(len(api_keys_to_use)):
            if provider == "cv":
                current_key = get_current_cv_key()
            elif provider == "bm":
                current_key = get_current_bm_key()
            elif provider == "nry":
                current_key = get_current_nr_key()
            elif provider == "dahl":
                current_key = get_current_dahl_key()
            elif provider == "qc":
                current_key = get_current_qc_key_for_model(requested_qc_model)
            elif provider == "marketku":
                current_key = get_current_marketku_key()
            elif provider == "atomesus":
                current_key = get_current_atomesus_key()
            elif provider == "weize":
                current_key = get_current_weize_key()
            else:
                current_key = get_current_key()

            headers["Authorization"] = f"Bearer {current_key}"
            for h in ("x-api-key", "anthropic-version"):
                headers.pop(h, None)

            start_req_time = time.time()
            try:
                async with httpx.AsyncClient(timeout=300) as client:
                    resp = await client.post(
                        upstream_endpoint,
                        headers=headers,
                        json=upstream_req,
                    )
                if resp.status_code in (401, 402, 403, 404, 429, 500, 502, 503, 504):
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

                    if provider == "qc" and requested_qc_model:
                        # Per-model key rotation: try next key for this model first.
                        mark_qc_model_exhausted(current_key, requested_qc_model)
                        if rotate_qc_key_for_model(requested_qc_model):
                            print(f"[LOG] QC model {requested_qc_model} exhausted on key, trying next key for same model")
                            continue
                        # All keys exhausted for this model; try fallback model.
                        fallback = None
                        for m in QC_FALLBACK_ORDER:
                            if m != requested_qc_model and m in config_module.QWEN_CLOUD_MODELS and m not in avoided_qc_models:
                                if any(not config_module.is_qc_model_exhausted(k, m) for k in QC_API_KEYS):
                                    fallback = m
                                    break
                        if not fallback:
                            # Nothing outside the brain's avoid-list is available; fall
                            # back to it rather than failing the request outright.
                            for m in QC_FALLBACK_ORDER:
                                if m != requested_qc_model and m in config_module.QWEN_CLOUD_MODELS:
                                    if any(not config_module.is_qc_model_exhausted(k, m) for k in QC_API_KEYS):
                                        fallback = m
                                        break
                        if fallback:
                            print(f"[LOG] All QC keys exhausted for {requested_qc_model}, falling back to {fallback}")
                            requested_qc_model = fallback
                            upstream_req["model"] = fallback
                            log_model = f"qc/{fallback}"
                            model_switched = True
                            continue

                    if provider == "kc":
                        rotate_key()
                    elif provider == "cv":
                        rotate_cv_key()
                    elif provider == "bm":
                        rotate_bm_key()
                    elif provider == "nry":
                        rotate_nr_key()
                    elif provider == "dahl":
                        rotate_dahl_key()
                    elif provider == "qc":
                        rotate_qc_key()
                    elif provider == "marketku":
                        rotate_marketku_key()
                    elif provider == "atomesus":
                        rotate_atomesus_key()
                    elif provider == "weize":
                        rotate_weize_key()
                    last_error_status = resp.status_code
                    last_error_content = err_json or {"error": resp.text}
                    await sse_broadcaster.broadcast("status", await _build_status_dict())
                    continue
                if resp.status_code != 200:
                    try:
                        err_json = resp.json()
                    except Exception:
                        err_json = {"error": resp.text}
                    if resp.status_code == 400 and is_context_window_error(err_json) and c_idx < len(compact_levels) - 1:
                        print(f"[LOG] Context window exceeded, auto-compacting...")
                        context_window_hit = True
                        break
                    add_request_log(log_model, resp.status_code, current_key, rotated_occurred, int((time.time() - start_req_time) * 1000))
                    return JSONResponse(status_code=resp.status_code, content=err_json)
                openai_resp = resp.json()
                anthropic_resp = to_anthropic_response(openai_resp, log_model, msg_id)
                usage = anthropic_resp.get("usage", {})
                output_tokens = usage.get("output_tokens", 0)
                total_ms = int((time.time() - start_req_time) * 1000)
                add_request_log(log_model, 200, current_key, rotated_occurred, total_ms, input_tokens, output_tokens)

                if enable_brain and user_message_text:
                    await _save_brain_exchange(
                        session_id=session_id,
                        api_key_hash=api_key_hash_for_brain,
                        user_content=user_message_text,
                        assistant_content=anthropic_resp.get("content", []),
                        model=log_model,
                    )

                threshold = config_module.SLOW_RESPONSE_THRESHOLD_MS
                if threshold > 0 and total_ms > threshold and len(api_keys_to_use) > 1:
                    print(f"[LOG] Slow response {total_ms}ms > {threshold}ms, rotating {provider} key proactively")
                    if provider == "kc":
                        rotate_key(reason="Slow")
                    elif provider == "cv":
                        rotate_cv_key(reason="Slow")
                    elif provider == "bm":
                        rotate_bm_key(reason="Slow")
                    elif provider == "nry":
                        rotate_nr_key(reason="Slow")
                    elif provider == "qc":
                        # Per-model slow rotation: move to next key for this model
                        rotate_qc_key_for_model(requested_qc_model)
                    elif provider == "marketku":
                        rotate_marketku_key()
                    elif provider == "atomesus":
                        rotate_atomesus_key()
                    elif provider == "weize":
                        rotate_weize_key()
                    # Dahl upstream is inherently slow; don't rotate on slow total time
                await sse_broadcaster.broadcast("log", recent_requests[0] if recent_requests else {})
                await sse_broadcaster.broadcast("status", await _build_status_dict())
                return JSONResponse(anthropic_resp)
            except Exception as e:
                print(f"[LOG] Request attempt {attempt} with key {current_key[:10]}... failed: {type(e).__name__}: {str(e)}")

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
                elif provider == "nry":
                    rotate_nr_key()
                elif provider == "dahl":
                    rotate_dahl_key()
                elif provider == "qc":
                    rotate_qc_key()
                elif provider == "marketku":
                    rotate_marketku_key()
                elif provider == "atomesus":
                    rotate_atomesus_key()
                last_error_status = 500
                last_error_content = {"error": str(e)}
                await sse_broadcaster.broadcast("status", await _build_status_dict())

        # If we switched QC model due to exhaustion, re-scan from key index 0 for the new model
        if model_switched:
            model_switched = False
            continue

        if context_window_hit:
            continue

        return JSONResponse(status_code=last_error_status, content=last_error_content)

    return JSONResponse(
        status_code=400,
        content={"error": {"message": "Konteks terlalu panjang bahkan setelah auto-compact. Silakan mulai percakapan baru."}}
    )


def _aggregate_openai_sse(raw_text: str, fallback_model: str):
    """
    Reconstruct a normal chat.completion object from an SSE response body.

    Some upstreams ignore `stream: false` under certain conditions (large
    prompts, particular models) and stream anyway. resp.json() then fails,
    and without this the raw SSE text used to get dumped verbatim into an
    "error" field on an otherwise-200 response. Returns None if the text
    doesn't look like SSE data at all, so the caller can fall back to
    reporting a real error.
    """
    content_parts = []
    tool_calls = {}
    finish_reason = "stop"
    model_name = None
    usage = None
    saw_data = False

    for line in raw_text.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        data_str = line[6:].strip()
        if data_str == "[DONE]":
            continue
        try:
            chunk = json.loads(data_str)
        except Exception:
            continue
        saw_data = True
        model_name = chunk.get("model") or model_name
        if chunk.get("usage"):
            usage = chunk["usage"]
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]
        delta = choice.get("delta") or {}
        if delta.get("content"):
            content_parts.append(delta["content"])
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            slot = tool_calls.setdefault(idx, {"id": tc.get("id"), "type": "function", "function": {"name": "", "arguments": ""}})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                slot["function"]["arguments"] += fn["arguments"]

    if not saw_data:
        return None

    message = {"role": "assistant", "content": "".join(content_parts) or None}
    if tool_calls:
        message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
        finish_reason = "tool_calls"

    return {
        "id": f"chatcmpl-reconstructed-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name or fallback_model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage or {},
    }


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    OpenAI-compatible endpoint for OpenChamber and other OpenAI-compatible clients.
    Keeps the external API OpenAI-shaped while using the same provider prefixes as
    /v1/messages: kc, cv, bm, nry, dh, qc, mk, at, wz.
    """
    if not await _check_router_auth(request):
        return JSONResponse(status_code=401, content={"error": {"message": "Invalid API key"}})

    try:
        openai_payload = await request.json()
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": {"message": f"Invalid JSON: {str(e)}"}})

    payload = dict(openai_payload)
    requested_model = payload.get("model") or "cv/gpt-5.4-mini"
    print(f"[CHAT-COMPLETIONS] Model: {requested_model}, stream: {payload.get('stream')}", flush=True)

    for cprefix in config_module.CUSTOM_PROVIDERS:
        if requested_model.startswith(f"{cprefix}/"):
            model_name = requested_model[len(cprefix) + 1:]
            system_prompt, anthropic_messages = openai_to_anthropic_messages(payload.get("messages") or [])
            anthropic_payload = {
                "model": model_name,
                "messages": anthropic_messages,
                "max_tokens": payload.get("max_tokens", 4096),
            }
            if system_prompt:
                anthropic_payload["system"] = system_prompt
            want_stream = bool(payload.get("stream"))
            start_req_time = time.time()
            kind, status, body = await _dispatch_custom_provider(cprefix, anthropic_payload, want_stream)
            log_model = f"{cprefix}/{model_name}"
            add_request_log(log_model, status, "custom", False, int((time.time() - start_req_time) * 1000))

            if status != 200:
                return JSONResponse(status_code=status, content=body)
            if kind == "json":
                return JSONResponse(content=anthropic_to_openai_response(body, requested_model))

            async def _relay_openai_stream():
                # `body` yields either whole SSE-event strings (upstream was
                # openai-format, already reassembled by stream_as_anthropic)
                # or raw, possibly-partial byte chunks (upstream was
                # anthropic-format, relayed as-is) -- buffer defensively so
                # a line split across two chunks doesn't get silently dropped.
                idx = 0
                buffer = ""
                async for chunk in body:
                    text = chunk if isinstance(chunk, str) else chunk.decode(errors="ignore")
                    buffer += text
                    lines = buffer.split("\n")
                    buffer = lines.pop()
                    for line in lines:
                        line = line.strip()
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str in ("[DONE]", ""):
                            continue
                        try:
                            chunk_data = json.loads(data_str)
                        except Exception:
                            continue
                        out = anthropic_to_openai_stream_chunk(chunk_data, requested_model, idx)
                        idx += 1
                        if out:
                            yield out.encode()
                yield b"data: [DONE]\n\n"

            return StreamingResponse(_relay_openai_stream(), media_type="text/event-stream")

    provider = "cv"

    if requested_model.startswith("cv/") or requested_model in config_module.CAVOTI_MODELS:
        provider = "cv"
    elif requested_model.startswith("bm/") or requested_model in config_module.BLUESMINDS_MODELS:
        provider = "bm"
    elif requested_model.startswith("nry/") or requested_model in config_module.NARA_MODELS:
        provider = "nry"
    elif requested_model.startswith("dh/") or requested_model in config_module.DAHL_MODELS_SHORT:
        provider = "dahl"
    elif requested_model.startswith("qc/"):
        provider = "qc"
    elif requested_model.startswith("mk/") or requested_model in config_module.MARKETKU_MODELS:
        provider = "marketku"
    elif requested_model.startswith("at/") or requested_model in config_module.ATOMESUS_MODELS:
        provider = "atomesus"
    elif requested_model.startswith("wz/") or requested_model in config_module.WEIZE_MODELS:
        provider = "weize"
    elif requested_model.startswith("kc/") or requested_model in config_module.KIMCHI_MODELS:
        provider = "kc"

    if provider in config_module.DISABLED_PROVIDERS:
        return JSONResponse(status_code=503, content={"error": {"message": f"Provider '{provider}' has been removed."}})

    provider_prefixes = {
        "kc": "kc/",
        "cv": "cv/",
        "bm": "bm/",
        "nry": "nry/",
        "dahl": "dh/",
        "qc": "qc/",
        "marketku": "mk/",
        "atomesus": "at/",
        "weize": "wz/",
    }
    prefix = provider_prefixes.get(provider)
    upstream_model = requested_model[len(prefix):] if prefix and requested_model.startswith(prefix) else requested_model
    if provider == "dahl":
        upstream_model = resolve_dahl_model(upstream_model)
    payload["model"] = upstream_model

    if provider == "cv":
        upstream_base_url = CAVOTI_BASE_URL
        api_keys_to_use = CV_API_KEYS or ([CAVOTI_API_KEY] if CAVOTI_API_KEY else [])
        get_key = get_current_cv_key
        rotate = rotate_cv_key
    elif provider == "bm":
        upstream_base_url = BLUESMINDS_BASE_URL
        api_keys_to_use = BM_API_KEYS or ([BLUESMINDS_API_KEY] if BLUESMINDS_API_KEY else [])
        get_key = get_current_bm_key
        rotate = rotate_bm_key
    elif provider == "nry":
        upstream_base_url = NARA_BASE_URL
        api_keys_to_use = NR_API_KEYS
        get_key = get_current_nr_key
        rotate = rotate_nr_key
    elif provider == "dahl":
        upstream_base_url = DAHL_BASE_URL
        api_keys_to_use = DAHL_API_KEYS
        get_key = get_current_dahl_key
        rotate = rotate_dahl_key
    elif provider == "qc":
        upstream_base_url = QWEN_CLOUD_BASE_URL
        api_keys_to_use = QC_API_KEYS
        get_key = get_current_qc_key
        rotate = rotate_qc_key
    elif provider == "marketku":
        upstream_base_url = MARKETKU_BASE_URL
        api_keys_to_use = MARKETKU_API_KEYS
        get_key = get_current_marketku_key
        rotate = rotate_marketku_key
    elif provider == "atomesus":
        upstream_base_url = ATOMESUS_BASE_URL
        api_keys_to_use = ATOMESUS_API_KEYS
        get_key = get_current_atomesus_key
        rotate = rotate_atomesus_key
    elif provider == "weize":
        upstream_base_url = WEIZE_BASE_URL
        api_keys_to_use = WEIZE_API_KEYS
        get_key = get_current_weize_key
        rotate = rotate_weize_key
    else:
        upstream_base_url = DEFAULT_UPSTREAM_URL
        api_keys_to_use = API_KEYS
        get_key = get_current_key
        rotate = rotate_key

    if not api_keys_to_use:
        return JSONResponse(status_code=500, content={"error": {"message": "No upstream API keys available"}})

    messages = payload.get("messages") or []
    user_message_text = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                user_message_text = content
            elif isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif isinstance(block.get("text"), str):
                            text_parts.append(block.get("text"))
                    elif isinstance(block, str):
                        text_parts.append(block)
                user_message_text = "\n".join(text_parts)
            break

    auth_header = request.headers.get("Authorization", "")
    x_api_key = request.headers.get("x-api-key", "")
    client_credential = auth_header[7:] if auth_header.startswith("Bearer ") else x_api_key
    api_key_hash_for_brain = BrainMiddleware.get_api_key_hash(client_credential)
    session_id_header = request.headers.get("X-Session-Id")
    if not session_id_header:
        session_id_header = BrainMiddleware.get_api_key_hash(
            f"{api_key_hash_for_brain}:{user_message_text[:200]}"
        )[:16]

    from app.database import get_or_create_session
    session_id = await get_or_create_session(
        identifier=session_id_header,
        api_key_hash=api_key_hash_for_brain,
        model=requested_model,
    )

    if user_message_text:
        brain_context = await BrainMiddleware.build_brain_context(
            api_key_hash=api_key_hash_for_brain,
            user_message=user_message_text,
            session_id=session_id,
            enable_brain=True,
        )
        if brain_context:
            payload_messages = list(messages)
            system_index = next((i for i, msg in enumerate(payload_messages) if msg.get("role") == "system"), None)
            if system_index is None:
                payload_messages.insert(0, {"role": "system", "content": brain_context.strip()})
            else:
                existing = payload_messages[system_index].get("content", "")
                payload_messages[system_index] = {
                    **payload_messages[system_index],
                    "content": f"{existing}{brain_context}" if isinstance(existing, str) else brain_context.strip(),
                }
            payload["messages"] = payload_messages

    upstream_endpoint = f"{upstream_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "iyan-router/openai-compatible",
    }

    last_status = 429
    last_content = {"error": {"message": "All configured API keys are rate limited or unauthorized."}}

    for attempt in range(len(api_keys_to_use)):
        current_key = get_key()
        headers["Authorization"] = f"Bearer {current_key}"
        start_req_time = time.time()

        try:
            if payload.get("stream"):
                print(f"[STREAM-INIT] Entering stream mode for model: {requested_model}, stream={payload.get('stream')}", flush=True)

                async def stream_openai():
                    import re
                    should_strip = requested_model.endswith(("-thinking", "-agentic", "-thinking-agentic"))
                    buffer = ""
                    inside_thinking = False

                    print(f"[STREAM] Model: {requested_model}, should_strip: {should_strip}", flush=True)

                    async with httpx.AsyncClient(timeout=300) as client:
                        async with client.stream("POST", upstream_endpoint, headers=headers, json=payload) as resp:
                            if resp.status_code != 200:
                                error_text = (await resp.aread()).decode(errors="replace")
                                yield f"data: {json.dumps({'error': {'message': error_text}})}\n\n"
                                return

                            async for chunk in resp.aiter_bytes():
                                if not should_strip:
                                    yield chunk
                                    continue

                                # Decode and parse SSE chunks
                                text = chunk.decode('utf-8', errors='ignore')
                                buffer += text

                                # Process complete lines
                                lines = buffer.split('\n')
                                buffer = lines[-1]  # Keep incomplete line in buffer

                                for line in lines[:-1]:
                                    if line.startswith('data: '):
                                        try:
                                            data = json.loads(line[6:])
                                            if 'choices' in data and len(data['choices']) > 0:
                                                delta = data['choices'][0].get('delta', {})
                                                content = delta.get('content', '')

                                                if content:
                                                    original = content
                                                    # Track thinking tag state
                                                    if '<thinking>' in content:
                                                        inside_thinking = True
                                                    if '</thinking>' in content:
                                                        inside_thinking = False
                                                        content = re.sub(r'<thinking>.*?</thinking>', '', content, flags=re.DOTALL)
                                                    elif inside_thinking or '<thinking>' in content:
                                                        content = re.sub(r'<thinking>.*', '', content, flags=re.DOTALL)

                                                    if original != content:
                                                        logger.info(f"[STREAM] Stripped: {repr(original)} -> {repr(content)}")

                                                    # Update content in response
                                                    data['choices'][0]['delta']['content'] = content
                                                    if content:  # Only yield if there's content after stripping
                                                        yield f"data: {json.dumps(data)}\n".encode('utf-8')
                                                else:
                                                    yield f"{line}\n".encode('utf-8')
                                            else:
                                                yield f"{line}\n".encode('utf-8')
                                        except Exception as e:
                                            logger.warning(f"[STREAM] Parse error: {e}")
                                            yield f"{line}\n".encode('utf-8')
                                    else:
                                        yield f"{line}\n".encode('utf-8')

                return StreamingResponse(stream_openai(), media_type="text/event-stream")

            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(upstream_endpoint, headers=headers, json=payload)

            effective_status = resp.status_code
            try:
                content = resp.json()
            except Exception:
                aggregated = _aggregate_openai_sse(resp.text, requested_model) if resp.status_code == 200 else None
                if aggregated is not None:
                    print(f"[CHAT-COMPLETIONS] {requested_model}: upstream ignored stream=false, reconstructed from SSE", flush=True)
                    content = aggregated
                else:
                    content = {"error": {"message": resp.text}}
                    if resp.status_code == 200:
                        # Upstream claimed success but sent something we
                        # couldn't parse or reconstruct -- don't let that
                        # masquerade as a real 200 to the client.
                        effective_status = 502

            if effective_status == 200:
                choice = (content.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                assistant_content = message.get("content")
                if not assistant_content and message.get("tool_calls"):
                    assistant_content = json.dumps(message.get("tool_calls"))

                # Strip thinking tags for -thinking/-agentic models in OpenAI format
                if assistant_content and isinstance(assistant_content, str):
                    if requested_model.endswith(("-thinking", "-agentic", "-thinking-agentic")):
                        import re
                        assistant_content = re.sub(r'<thinking>.*?</thinking>\s*', '', assistant_content, flags=re.DOTALL)
                        # Update content in response
                        if "choices" in content and len(content["choices"]) > 0:
                            if "message" in content["choices"][0]:
                                content["choices"][0]["message"]["content"] = assistant_content

                if user_message_text:
                    await _save_brain_exchange(
                        session_id=session_id,
                        api_key_hash=api_key_hash_for_brain,
                        user_content=user_message_text,
                        assistant_content=assistant_content,
                        model=requested_model,
                    )
                add_request_log(requested_model, 200, current_key, False, int((time.time() - start_req_time) * 1000))
                await sse_broadcaster.broadcast("log", recent_requests[0] if recent_requests else {})
                await sse_broadcaster.broadcast("status", await _build_status_dict())
                return JSONResponse(content)

            last_status = effective_status
            last_content = content
            add_request_log(requested_model, effective_status, current_key, True, int((time.time() - start_req_time) * 1000))
            if effective_status in (401, 402, 403, 404, 429, 500, 502, 503, 504) and attempt < len(api_keys_to_use) - 1:
                rotate()
                continue
            return JSONResponse(status_code=effective_status, content=content)
        except Exception as e:
            last_status = 500
            last_content = {"error": {"message": str(e)}}
            add_request_log(requested_model, 500, current_key, True, int((time.time() - start_req_time) * 1000))
            if attempt < len(api_keys_to_use) - 1:
                rotate()
                continue
            return JSONResponse(status_code=500, content=last_content)

    return JSONResponse(status_code=last_status, content=last_content)

