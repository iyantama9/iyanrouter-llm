# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "fastapi",
#   "httpx",
#   "uvicorn",
#   "python-dotenv",
# ]
# ///

import json
import uuid
import os
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

load_dotenv()

app = FastAPI()

KIMCHI_BASE_URL = "https://llm.kimchi.dev/openai/v1"
API_KEY = os.environ["CASTAI_API_KEY"]


def flatten_content(content):
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and "text" in part:
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(filter(None, parts))
    return content or ""


def convert_tools(anthropic_tools):
    if not anthropic_tools:
        return []
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        }
        for t in anthropic_tools
    ]


def to_openai_messages(body):
    messages = []

    system = body.get("system")
    if system:
        messages.append({"role": "system", "content": flatten_content(system)})

    for msg in body.get("messages", []):
        role = msg["role"]
        content = msg.get("content", "")

        if role == "assistant" and isinstance(content, list):
            tool_calls = []
            text_parts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    })
                elif block.get("type") == "text" and block.get("text"):
                    text_parts.append(block["text"])
            if tool_calls:
                openai_msg = {"role": "assistant", "tool_calls": tool_calls}
                if text_parts:
                    openai_msg["content"] = " ".join(text_parts)
                messages.append(openai_msg)
            else:
                messages.append({"role": "assistant", "content": flatten_content(content)})

        elif role == "user" and isinstance(content, list):
            tool_results = []
            text_parts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": flatten_content(block.get("content", "")),
                    })
                elif "text" in block and block.get("text"):
                    text_parts.append(block["text"])
            if tool_results:
                if text_parts:
                    messages.append({"role": "user", "content": " ".join(text_parts)})
                messages.extend(tool_results)
            else:
                messages.append({"role": "user", "content": flatten_content(content)})

        else:
            messages.append({"role": role, "content": flatten_content(content)})

    return messages


def build_openai_request(body):
    req = {
        "model": body.get("model", "kimi-k2.6"),
        "messages": to_openai_messages(body),
        "stream": body.get("stream", False),
    }
    if "max_tokens" in body:
        req["max_tokens"] = body["max_tokens"]
    if "temperature" in body:
        req["temperature"] = body["temperature"]

    tools = convert_tools(body.get("tools"))
    if tools:
        req["tools"] = tools

    tc = body.get("tool_choice")
    if tc and isinstance(tc, dict):
        if tc.get("type") == "auto":
            req["tool_choice"] = "auto"
        elif tc.get("type") == "any":
            req["tool_choice"] = "required"
        elif tc.get("type") == "tool":
            req["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}

    return req


def to_anthropic_response(openai_resp, model, msg_id):
    choice = openai_resp["choices"][0]
    message = choice["message"]
    usage = openai_resp.get("usage", {})
    finish_reason = choice.get("finish_reason", "stop")

    content = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    for tc in message.get("tool_calls") or []:
        try:
            input_data = json.loads(tc["function"]["arguments"])
        except Exception:
            input_data = {}
        content.append({
            "type": "tool_use",
            "id": tc["id"],
            "name": tc["function"]["name"],
            "input": input_data,
        })

    stop_reason = "tool_use" if finish_reason == "tool_calls" else "end_turn"
    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


async def stream_as_anthropic(openai_stream, model, msg_id):
    tool_calls = {}   # openai index -> {id, name, arguments, block_index}
    text_opened = False
    next_block = 0
    finish_reason = "stop"
    output_tokens = 0

    yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'content': [], 'model': model, 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 0, 'output_tokens': 1}}})}\n\n"
    yield f"event: ping\ndata: {json.dumps({'type': 'ping'})}\n\n"

    async for line in openai_stream.aiter_lines():
        if not line.startswith("data: "):
            continue
        raw = line[6:]
        if raw.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(raw)
        except Exception:
            continue

        choice = chunk["choices"][0]
        delta = choice.get("delta", {})
        fr = choice.get("finish_reason")
        if fr:
            finish_reason = fr

        text = delta.get("content")
        if text:
            if not text_opened:
                text_opened = True
                yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': next_block, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                next_block += 1
            output_tokens += 1
            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text}})}\n\n"

        for tc_delta in delta.get("tool_calls") or []:
            idx = tc_delta.get("index", 0)
            if idx not in tool_calls:
                block_index = next_block
                next_block += 1
                tool_calls[idx] = {
                    "id": tc_delta.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                    "name": tc_delta.get("function", {}).get("name", ""),
                    "arguments": "",
                    "block_index": block_index,
                }
                yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': block_index, 'content_block': {'type': 'tool_use', 'id': tool_calls[idx]['id'], 'name': tool_calls[idx]['name'], 'input': {}}})}\n\n"

            args_delta = tc_delta.get("function", {}).get("arguments", "")
            if args_delta:
                tool_calls[idx]["arguments"] += args_delta
                output_tokens += 1
                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': tool_calls[idx]['block_index'], 'delta': {'type': 'input_json_delta', 'partial_json': args_delta}})}\n\n"

    if text_opened:
        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
    for tc in tool_calls.values():
        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': tc['block_index']})}\n\n"

    stop_reason = "tool_use" if finish_reason == "tool_calls" else "end_turn"
    yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None}, 'usage': {'output_tokens': output_tokens}})}\n\n"
    yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"


@app.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()
    model = body.get("model", "kimi-k2.6")
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    openai_req = build_openai_request(body)
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    if openai_req.get("stream"):
        async def generate():
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream(
                    "POST",
                    f"{KIMCHI_BASE_URL}/chat/completions",
                    headers=headers,
                    json=openai_req,
                ) as resp:
                    async for chunk in stream_as_anthropic(resp, model, msg_id):
                        yield chunk
        return StreamingResponse(generate(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{KIMCHI_BASE_URL}/chat/completions",
            headers=headers,
            json=openai_req,
        )
    return JSONResponse(to_anthropic_response(resp.json(), model, msg_id))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4000)
