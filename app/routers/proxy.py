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
    ROUTER_PASSWORD, get_current_key, rotate_key, API_KEYS, NARA_BASE_URL, DAHL_BASE_URL, QWEN_CLOUD_BASE_URL, MARKETKU_BASE_URL,
    KIMCHI_MODELS, CAVOTI_MODELS, BLUESMINDS_MODELS, NARA_MODELS, DAHL_MODELS, DAHL_MODELS_SHORT, resolve_dahl_model, QWEN_CLOUD_MODELS, MARKETKU_MODELS, CV_API_KEYS, BM_API_KEYS, NR_API_KEYS, DAHL_API_KEYS, QC_API_KEYS, MARKETKU_API_KEYS,
    get_current_cv_key, rotate_cv_key, get_current_bm_key, rotate_bm_key, get_current_nr_key, rotate_nr_key, get_current_dahl_key, rotate_dahl_key, get_current_qc_key, rotate_qc_key, get_current_marketku_key, rotate_marketku_key,
    get_current_qc_key_for_model, rotate_qc_key_for_model, mark_qc_model_exhausted, QC_FALLBACK_ORDER,
    recent_requests, add_request_log,
)
from app.translator import (
    build_openai_request, to_anthropic_response, stream_as_anthropic, compact_messages,
    is_context_window_error, to_anthropic_stream_error, estimate_tokens,
)
from app.sse import sse_broadcaster


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


def _check_router_auth(request: Request):
    auth_header = request.headers.get("Authorization")
    x_api_key = request.headers.get("x-api-key")
    if ROUTER_PASSWORD and auth_header != f"Bearer {ROUTER_PASSWORD}" and x_api_key != ROUTER_PASSWORD:
        return False
    return True


@router.get("/v1/models")
@router.get("/models")
async def list_models(request: Request):
    if not _check_router_auth(request):
        return JSONResponse(status_code=401, content={"error": {"message": "Invalid router password."}})

    models = []
    for m in KIMCHI_MODELS:
        models.append(f"kc/{m}")
    for m in CAVOTI_MODELS:
        models.append(f"cv/{m}")
    for m in NARA_MODELS:
        models.append(f"nry/{m}")
    for m in DAHL_MODELS:
        models.append(f"dh/{m}")
    for m in QWEN_CLOUD_MODELS:
        models.append(f"qc/{m}")
    for m in MARKETKU_MODELS:
        models.append(m)

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
    if not _check_router_auth(request):
        return JSONResponse(status_code=401, content={"error": {"message": "Invalid router password."}})

    payload = await request.json()
    provider = "kc"
    if payload.get("model"):
        if payload["model"].startswith("cv/") or payload["model"] in CAVOTI_MODELS:
            provider = "cv"
        elif payload["model"].startswith("bm/") or payload["model"] in BLUESMINDS_MODELS:
            provider = "bm"
        elif payload["model"].startswith("nry/") or payload["model"] in NARA_MODELS:
            provider = "nry"
        elif payload["model"].startswith("dh/") or payload["model"] in DAHL_MODELS_SHORT:
            provider = "dahl"
        elif payload["model"].startswith("qc/"):
            provider = "qc"
        elif payload["model"].startswith("mk/") or payload["model"] in MARKETKU_MODELS:
            provider = "marketku"
        elif payload["model"].startswith("kc/") or payload["model"] in KIMCHI_MODELS:
            provider = "kc"

    for prefix in ("kc/", "cv/", "bm/", "nry/", "dh/", "qc/", "mk/"):
        if payload.get("model", "").startswith(prefix):
            payload["model"] = payload["model"][len(prefix):]
            break

    if provider == "dahl":
        payload["model"] = resolve_dahl_model(payload["model"])

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

    if upstream_req.get("stream"):
        async def generate():
            nonlocal requested_qc_model
            nonlocal log_model
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
                                            if m != requested_qc_model and m in QWEN_CLOUD_MODELS:
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
                                    yield chunk
                                total_ms = int((time.time() - start_req_time) * 1000)
                                ttft_ms = int((first_token_time - start_req_time) * 1000) if first_token_time else total_ms
                                add_request_log(log_model, 200, current_key, rotated_occurred, total_ms, input_tokens, token_tracker["output_tokens"])
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
                            if m != requested_qc_model and m in QWEN_CLOUD_MODELS:
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
