import json
import uuid
from app.config import SHOW_REASONING

def flatten_content(content):
    if not isinstance(content, list):
        return content or ""
    parts = [p.get("text", "") if isinstance(p, dict) else p for p in content if isinstance(p, (dict, str))]
    return "\n".join(filter(None, parts))

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
            final_content = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": flatten_content(block.get("content", "")),
                    })
                elif block.get("type") == "image":
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        final_content.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{source.get('media_type', 'image/jpeg')};base64,{source.get('data', '')}"
                            }
                        })
                elif "text" in block and block.get("text"):
                    final_content.append({"type": "text", "text": block["text"]})
            if tool_results:
                messages.extend(tool_results)
                if final_content:
                    messages.append({"role": "user", "content": final_content})
            else:
                if final_content:
                    messages.append({"role": "user", "content": final_content})
                else:
                    messages.append({"role": "user", "content": flatten_content(content)})

        else:
            messages.append({"role": role, "content": flatten_content(content)})

    return messages

import app.config as config

_ROUTER_BEHAVIOR = """\
<router_behavior>
You are an AI coding assistant. Apply these rules based on context:

THINKING: For complex tasks (multi-step implementation, debugging, architecture, analysis) — reason inside <thinking>…</thinking> before answering. For simple or conversational requests — skip it.

TASK STRUCTURE: When implementing, fixing, or refactoring across multiple steps or files, label each unit of work:
**Task 1 — [title]**
[work]
**Task 2 — [title]**
[work]
End with a **Summary** of all changes made.

FORMAT: Always use fenced code blocks with language identifiers. Never truncate code — always complete every function and file. Use clear headers for long responses.
</router_behavior>

"""


def normalize_for_qwen(messages):
    """Normalize messages for Qwen Cloud API compatibility.

    Qwen Cloud has stricter content format requirements:
    - User messages must have string content (no arrays)
    - Tool messages must have string content
    - Assistant messages with tool_calls must have string or null content
    - Images should be converted to placeholder text
    """
    normalized = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        # Flatten user message arrays to strings
        if role == "user" and isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "image_url":
                        img_url = block.get("image_url", {}).get("url", "")
                        if img_url.startswith("data:"):
                            text_parts.append("[Image attached - base64 data]")
                        else:
                            text_parts.append(f"[Image: {img_url}]")
            msg["content"] = "\n".join(filter(None, text_parts)) or "[No text content]"

        # Flatten tool message content to strings
        elif role == "tool" and isinstance(content, list):
            text_parts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
            msg["content"] = "\n".join(filter(None, text_parts)) or "[Tool result]"

        # Ensure assistant messages with tool_calls have proper content
        elif role == "assistant":
            if isinstance(content, list):
                text_parts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
                msg["content"] = "\n".join(filter(None, text_parts)) or None
            # If assistant has tool_calls but no content, set to None (OpenAI spec)
            if "tool_calls" in msg and not msg.get("content"):
                msg["content"] = None

        normalized.append(msg)

    return normalized


def build_openai_request(body, provider="kc"):
    claude_model = body.get("model", "")

    if provider in ("bm", "cv", "dahl", "nry", "qc", "marketku"):
        openai_model = claude_model
    else:
        fallback = config.KIMCHI_MODELS[-1] if config.KIMCHI_MODELS else ""
        model_str = claude_model.lower()

        if "sonnet" in model_str:
            openai_model = config.KIMCHI_MODELS[-1] if len(config.KIMCHI_MODELS) >= 1 else fallback
        elif "haiku" in model_str:
            openai_model = config.KIMCHI_MODELS[-2] if len(config.KIMCHI_MODELS) >= 2 else fallback
        elif "opus" in model_str or "grok" in model_str:
            openai_model = config.KIMCHI_MODELS[1] if len(config.KIMCHI_MODELS) >= 2 else fallback
        else:
            openai_model = claude_model if claude_model in config.KIMCHI_MODELS else fallback

    messages = to_openai_messages(body)

    # Qwen Cloud requires simplified content format
    if provider == "qc":
        messages = normalize_for_qwen(messages)

    if config.AUGMENT_SYSTEM_PROMPT:
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] = _ROUTER_BEHAVIOR + messages[0]["content"]
        else:
            messages.insert(0, {"role": "system", "content": _ROUTER_BEHAVIOR.strip()})

    req = {
        "model": openai_model,
        "messages": messages,
        "stream": body.get("stream", False),
    }
    if "max_tokens" in body:
        # Cap max_tokens to prevent context window overflow (model limit 196608)
        req["max_tokens"] = min(int(body["max_tokens"]), 16384)
    if "temperature" in body:
        # Limit temperature to max 0.2 for deterministic coding tool executions
        req["temperature"] = min(float(body["temperature"]), 0.2)

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
    if "error" in openai_resp:
        raise ValueError(openai_resp["error"].get("message", "Unknown upstream error"))
    choice = openai_resp["choices"][0]
    message = choice["message"]
    usage = openai_resp.get("usage", {})
    finish_reason = choice.get("finish_reason", "stop")

    content = []

    # Extract reasoning_content and format as native Anthropic thinking block
    reasoning = message.get("reasoning_content")
    if reasoning and SHOW_REASONING:
        content.append({"type": "thinking", "thinking": reasoning})

    content_str = message.get("content", "")
    if content_str:
        import re
        # Try matching complete tag first
        match = re.search(r'<(think|thinking|thought|thoughts|thinking_process)\b[^>]*>(.*?)</\1>(.*)', content_str, re.DOTALL | re.IGNORECASE)
        if match:
            pre_text = content_str[:match.start()]
            if pre_text:
                content.append({"type": "text", "text": pre_text})
            extracted_reasoning = match.group(2).strip()
            remaining_text = match.group(3).strip()
            if extracted_reasoning:
                content.append({"type": "thinking", "thinking": extracted_reasoning})
            if remaining_text:
                content.append({"type": "text", "text": remaining_text})
        else:
            # Fallback for unclosed tag
            match_open = re.search(r'<(think|thinking|thought|thoughts|thinking_process)\b[^>]*>(.*)', content_str, re.DOTALL | re.IGNORECASE)
            if match_open:
                pre_text = content_str[:match_open.start()]
                if pre_text:
                    content.append({"type": "text", "text": pre_text})
                extracted_reasoning = match_open.group(2).strip()
                if extracted_reasoning:
                    content.append({"type": "thinking", "thinking": extracted_reasoning})
            else:
                content.append({"type": "text", "text": content_str})

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

def filter_anthropic_response(anthropic_resp, model):
    import re
    content = []
    for block in anthropic_resp.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "")
            match = re.search(r'<(think|thinking|thought|thoughts|thinking_process)\b[^>]*>(.*?)</\1>(.*)', text, re.DOTALL | re.IGNORECASE)
            if match:
                pre_text = text[:match.start()]
                if pre_text:
                    content.append({"type": "text", "text": pre_text})
                extracted = match.group(2).strip()
                if extracted:
                    content.append({"type": "thinking", "thinking": extracted})
                remaining = match.group(3).strip()
                if remaining:
                    content.append({"type": "text", "text": remaining})
            else:
                match_open = re.search(r'<(think|thinking|thought|thoughts|thinking_process)\b[^>]*>(.*)', text, re.DOTALL | re.IGNORECASE)
                if match_open:
                    pre_text = text[:match_open.start()]
                    if pre_text:
                        content.append({"type": "text", "text": pre_text})
                    extracted = match_open.group(2).strip()
                    if extracted:
                        content.append({"type": "thinking", "thinking": extracted})
                else:
                    content.append(block)
        else:
            content.append(block)
    
    anthropic_resp["content"] = content
    anthropic_resp["model"] = model
    return anthropic_resp

async def stream_anthropic_filter(anthropic_stream_iter):
    import re
    text_buffer = ""
    in_text_thinking = False
    reasoning_opened = False
    reasoning_closed = False
    text_opened = False
    next_block = 0
    reasoning_block_idx = None
    text_block_idx = None
    
    async for line in anthropic_stream_iter:
        if isinstance(line, bytes):
            line = line.decode('utf-8', errors='replace')
        if not line.startswith("data: "):
            yield f"{line}\n\n"
            continue
            
        raw = line[6:].strip()
        if raw == "[DONE]":
            if len(text_buffer) > 0:
                if not text_opened:
                    text_opened = True
                    text_block_idx = next_block
                    next_block += 1
                    yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': text_block_idx, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': text_block_idx, 'delta': {'type': 'text_delta', 'text': text_buffer}})}\n\n"
            
            if reasoning_opened and not reasoning_closed:
                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': reasoning_block_idx})}\n\n"
            if text_opened:
                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': text_block_idx})}\n\n"
            yield f"{line}\n\n"
            continue
            
        try:
            event = json.loads(raw)
        except Exception:
            yield f"{line}\n\n"
            continue
            
        evt_type = event.get("type")
        
        if evt_type in ("message_start", "message_delta", "message_stop", "ping"):
            yield f"{line}\n\n"
        elif evt_type == "content_block_start":
            block = event.get("content_block", {})
            if block.get("type") == "text":
                continue # Skip emitting, we will emit later
            else:
                if len(text_buffer) > 0:
                    if not text_opened:
                        text_opened = True
                        text_block_idx = next_block
                        next_block += 1
                        yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': text_block_idx, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                    yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': text_block_idx, 'delta': {'type': 'text_delta', 'text': text_buffer}})}\n\n"
                    text_buffer = ""
                if reasoning_opened and not reasoning_closed:
                    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': reasoning_block_idx})}\n\n"
                    reasoning_closed = True
                if text_opened:
                    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': text_block_idx})}\n\n"
                    text_opened = False
                
                event["index"] = next_block
                next_block += 1
                yield f"event: content_block_start\ndata: {json.dumps(event)}\n\n"
        elif evt_type == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                text_buffer += text
                while True:
                    if not in_text_thinking:
                        match_open = re.search(r'<(think|thinking|thought|thoughts|thinking_process)\b[^>]*>', text_buffer, re.IGNORECASE)
                        if match_open:
                            i = match_open.start()
                            tag_len = len(match_open.group(0))
                            pre_text = text_buffer[:i]
                            if pre_text:
                                if reasoning_opened and not reasoning_closed:
                                    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': reasoning_block_idx})}\n\n"
                                    reasoning_closed = True
                                if not text_opened:
                                    text_opened = True
                                    text_block_idx = next_block
                                    next_block += 1
                                    yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': text_block_idx, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': text_block_idx, 'delta': {'type': 'text_delta', 'text': pre_text}})}\n\n"
                            
                            if not reasoning_opened or reasoning_closed:
                                reasoning_opened = True
                                reasoning_closed = False
                                reasoning_block_idx = next_block
                                next_block += 1
                                yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': reasoning_block_idx, 'content_block': {'type': 'thinking', 'thinking': ''}})}\n\n"
                            
                            in_text_thinking = True
                            text_buffer = text_buffer[i + tag_len:]
                        else:
                            if len(text_buffer) > 20:
                                safe_send = text_buffer[:-20]
                                text_buffer = text_buffer[-20:]
                                if reasoning_opened and not reasoning_closed:
                                    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': reasoning_block_idx})}\n\n"
                                    reasoning_closed = True
                                if not text_opened:
                                    text_opened = True
                                    text_block_idx = next_block
                                    next_block += 1
                                    yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': text_block_idx, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': text_block_idx, 'delta': {'type': 'text_delta', 'text': safe_send}})}\n\n"
                            break
                    else:
                        match_close = re.search(r'</(think|thinking|thought|thoughts|thinking_process)\b[^>]*>', text_buffer, re.IGNORECASE)
                        if match_close:
                            i = match_close.start()
                            tag_len = len(match_close.group(0))
                            pre_reasoning = text_buffer[:i]
                            if pre_reasoning:
                                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': reasoning_block_idx, 'delta': {'type': 'thinking_delta', 'thinking': pre_reasoning}})}\n\n"
                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': reasoning_block_idx})}\n\n"
                            reasoning_closed = True
                            in_text_thinking = False
                            text_buffer = text_buffer[i + tag_len:]
                        else:
                            if len(text_buffer) > 20:
                                safe_send = text_buffer[:-20]
                                text_buffer = text_buffer[-20:]
                                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': reasoning_block_idx, 'delta': {'type': 'thinking_delta', 'thinking': safe_send}})}\n\n"
                            break
            else:
                event["index"] = next_block - 1
                yield f"event: content_block_delta\ndata: {json.dumps(event)}\n\n"
        elif evt_type == "content_block_stop":
            pass
        else:
            yield f"{line}\n\n"

async def stream_as_anthropic(openai_stream, model, msg_id, input_tokens=0, token_tracker=None):
    tool_calls = {}
    reasoning_opened = False
    reasoning_closed = False
    text_opened = False
    next_block = 0
    finish_reason = "stop"
    output_tokens = 0
    reasoning_block_idx = None
    text_block_idx = None
    text_buffer = ""
    in_text_thinking = False

    # Check for error in first line before yielding message_start
    first_chunk = None
    stream_iter = openai_stream.aiter_lines()
    try:
        while True:
            line = await stream_iter.__anext__()
            if not line.startswith("data: "):
                continue
            raw = line[6:]
            if raw.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(raw)
                if "error" in chunk:
                    raise ValueError(chunk["error"].get("message", "Unknown upstream error"))
                first_chunk = chunk
                break
            except ValueError:
                raise
            except Exception:
                continue
    except StopAsyncIteration:
        pass

    yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'content': [], 'model': model, 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': input_tokens, 'output_tokens': 1}}})}\n\n"
    yield f"event: ping\ndata: {json.dumps({'type': 'ping'})}\n\n"

    async def get_chunks():
        if first_chunk is not None:
            yield first_chunk
        try:
            while True:
                line = await stream_iter.__anext__()
                if not line.startswith("data: "):
                    continue
                raw = line[6:]
                if raw.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                    if "error" in chunk:
                        raise ValueError(chunk["error"].get("message", "Unknown upstream error"))
                    yield chunk
                except ValueError:
                    raise
                except Exception:
                    continue
        except StopAsyncIteration:
            pass

    async for chunk in get_chunks():
        # Some providers (e.g. Cavoti) send a final usage-only chunk with empty choices list
        if not chunk.get("choices"):
            usage = chunk.get("usage", {})
            if usage.get("completion_tokens"):
                output_tokens = usage["completion_tokens"]
            continue
        choice = chunk["choices"][0]
        delta = choice.get("delta", {})
        fr = choice.get("finish_reason")
        if fr:
            finish_reason = fr

        # Stream reasoning content as native Anthropic thinking blocks (for models that support reasoning_content)
        reasoning = delta.get("reasoning_content")
        if reasoning and SHOW_REASONING:
            if not reasoning_opened:
                reasoning_opened = True
                reasoning_block_idx = next_block
                next_block += 1
                yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': reasoning_block_idx, 'content_block': {'type': 'thinking', 'thinking': ''}})}\n\n"
            output_tokens += 1
            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': reasoning_block_idx, 'delta': {'type': 'thinking_delta', 'thinking': reasoning}})}\n\n"

        text = delta.get("content")
        if text:
            text_buffer += text
            while True:
                if not in_text_thinking:
                    # Look for opening tags case-insensitively with regex
                    import re
                    match_open = re.search(r'<(think|thinking|thought|thoughts|thinking_process)\b[^>]*>', text_buffer, re.IGNORECASE)
                    if match_open:
                        i = match_open.start()
                        tag_len = len(match_open.group(0))
                        
                        # Send text before the tag
                        pre_text = text_buffer[:i]
                        if pre_text:
                            if reasoning_opened and not reasoning_closed:
                                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': reasoning_block_idx})}\n\n"
                                reasoning_closed = True
                            if not text_opened:
                                text_opened = True
                                text_block_idx = next_block
                                next_block += 1
                                yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': text_block_idx, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': text_block_idx, 'delta': {'type': 'text_delta', 'text': pre_text}})}\n\n"
                        
                        # Open reasoning block
                        if not reasoning_opened or reasoning_closed:
                            reasoning_opened = True
                            reasoning_closed = False
                            reasoning_block_idx = next_block
                            next_block += 1
                            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': reasoning_block_idx, 'content_block': {'type': 'thinking', 'thinking': ''}})}\n\n"
                        
                        in_text_thinking = True
                        text_buffer = text_buffer[i + tag_len:]
                    else:
                        # No complete tag found. Send safe text (all except last 20 chars to be safe with longer tags)
                        if len(text_buffer) > 20:
                            safe_send = text_buffer[:-20]
                            text_buffer = text_buffer[-20:]
                            if reasoning_opened and not reasoning_closed:
                                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': reasoning_block_idx})}\n\n"
                                reasoning_closed = True
                            if not text_opened:
                                text_opened = True
                                text_block_idx = next_block
                                next_block += 1
                                yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': text_block_idx, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': text_block_idx, 'delta': {'type': 'text_delta', 'text': safe_send}})}\n\n"
                        break
                else:
                    # Look for closing tags case-insensitively with regex
                    import re
                    match_close = re.search(r'</(think|thinking|thought|thoughts|thinking_process)\b[^>]*>', text_buffer, re.IGNORECASE)
                    if match_close:
                        i = match_close.start()
                        tag_len = len(match_close.group(0))
                        
                        # Send reasoning before the tag
                        pre_reasoning = text_buffer[:i]
                        if pre_reasoning:
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': reasoning_block_idx, 'delta': {'type': 'thinking_delta', 'thinking': pre_reasoning}})}\n\n"
                        
                        # Close reasoning block
                        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': reasoning_block_idx})}\n\n"
                        reasoning_closed = True
                        in_text_thinking = False
                        text_buffer = text_buffer[i + tag_len:]
                    else:
                        # No complete tag found. Send safe reasoning (all except last 20 chars)
                        if len(text_buffer) > 20:
                            safe_send = text_buffer[:-20]
                            text_buffer = text_buffer[-20:]
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': reasoning_block_idx, 'delta': {'type': 'thinking_delta', 'thinking': safe_send}})}\n\n"
                        break

        for tc_delta in delta.get("tool_calls") or []:
            # Close reasoning block before tool calls
            if reasoning_opened and not reasoning_closed:
                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': reasoning_block_idx})}\n\n"
                reasoning_closed = True

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

    # Flush any remaining text in the buffer
    if text_buffer:
        if in_text_thinking:
            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': reasoning_block_idx, 'delta': {'type': 'thinking_delta', 'thinking': text_buffer}})}\n\n"
        else:
            if reasoning_opened and not reasoning_closed:
                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': reasoning_block_idx})}\n\n"
                reasoning_closed = True
            if not text_opened:
                text_opened = True
                text_block_idx = next_block
                next_block += 1
                yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': text_block_idx, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': text_block_idx, 'delta': {'type': 'text_delta', 'text': text_buffer}})}\n\n"

    # Close any remaining open blocks
    if reasoning_opened and not reasoning_closed:
        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': reasoning_block_idx})}\n\n"

    if text_opened:
        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': text_block_idx})}\n\n"
    for tc in tool_calls.values():
        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': tc['block_index']})}\n\n"

    stop_reason = "tool_use" if finish_reason == "tool_calls" else "end_turn"
    yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None}, 'usage': {'output_tokens': output_tokens}})}\n\n"

    yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
    if token_tracker is not None:
        token_tracker["output_tokens"] = output_tokens


def compact_messages(messages, keep_last=20):
    """Auto-compact messages when context window is exceeded.
    Keeps system messages + last N conversation messages,
    cutting at safe boundaries (before user messages) to preserve tool call chains.
    """
    if len(messages) <= keep_last:
        return messages

    # Separate system messages from conversation
    system_msgs = []
    conversation = []
    for m in messages:
        if m.get("role") == "system":
            system_msgs.append(m)
        else:
            conversation.append(m)

    if len(conversation) <= keep_last:
        return messages

    # Find safe cut points (before user messages to preserve tool call sequences)
    safe_points = [i for i, m in enumerate(conversation) if m.get("role") == "user"]

    if not safe_points:
        # No user messages found, simple truncation from end
        kept = conversation[-keep_last:]
        cut_point = len(conversation) - keep_last
    else:
        # Find nearest safe point that keeps ~keep_last messages
        target_start = len(conversation) - keep_last
        cut_point = safe_points[0]
        for sp in safe_points:
            if sp >= target_start:
                cut_point = sp
                break
        kept = conversation[cut_point:]

    # Clean up orphaned tool messages to ensure every 'tool' message in the kept slice
    # has its corresponding 'assistant' message containing the tool call.
    while True:
        orphaned_found = False
        active_tool_call_ids = set()
        for m in kept:
            if m.get("role") == "assistant" and "tool_calls" in m:
                for tc in m["tool_calls"]:
                    active_tool_call_ids.add(tc.get("id"))

        for m in kept:
            if m.get("role") == "tool":
                tcid = m.get("tool_call_id")
                if tcid and tcid not in active_tool_call_ids:
                    # Found an orphaned tool message! Find its assistant caller in the original conversation.
                    assistant_idx = -1
                    for idx, conv_m in enumerate(conversation):
                        if conv_m.get("role") == "assistant" and "tool_calls" in conv_m:
                            if any(tc.get("id") == tcid for tc in conv_m["tool_calls"]):
                                assistant_idx = idx
                                break
                    
                    if assistant_idx != -1:
                        if assistant_idx < cut_point:
                            # Move the cut point back to include the assistant message
                            cut_point = assistant_idx
                            kept = conversation[cut_point:]
                            orphaned_found = True
                            break
                    else:
                        # If the assistant message doesn't exist at all, remove the orphaned tool message to prevent API crash
                        kept = [msg for msg in kept if msg.get("tool_call_id") != tcid]
                        orphaned_found = True
                        break
        
        if not orphaned_found:
            break

    result = system_msgs[:]
    if len(kept) < len(conversation):
        result.append({
            "role": "system",
            "content": "[Konteks percakapan sebelumnya telah diringkas otomatis karena melebihi batas token model. Lanjutkan dari konteks terbaru di bawah ini.]"
        })
    result.extend(kept)
    return result


def is_context_window_error(error_data):
    """Check if an error response is a context window exceeded error."""
    error_str = json.dumps(error_data).lower()
    return (
        "context length" in error_str or 
        "context_length_exceeded" in error_str or 
        "maximum context" in error_str or 
        "context window" in error_str or
        "context_window" in error_str or
        "contextlimit" in error_str or
        "context limit" in error_str or
        "length exceeded" in error_str
    )

def parse_input_tokens_from_error(error_data):
    """Extract input token count from a context window exceeded error message."""
    import re
    error_str = json.dumps(error_data)
    
    # Pattern 1: parameter=input_tokens, value=180225
    match = re.search(r'parameter=input_tokens,\s*value=(\d+)', error_str, re.IGNORECASE)
    if match:
        return int(match.group(1))
        
    # Pattern 2: prompt contains at least 180225 input tokens
    match = re.search(r'prompt contains at least (\d+) input tokens', error_str, re.IGNORECASE)
    if match:
        return int(match.group(1))
        
    # Pattern 3: 2000 in the messages/prompt
    match = re.search(r'(\d+)\s+in\s+the\s+(messages|prompt)', error_str, re.IGNORECASE)
    if match:
        return int(match.group(1))
        
    # Pattern 4: input_tokens: 180225
    match = re.search(r'input_tokens.*?(\d+)', error_str, re.IGNORECASE)
    if match:
        return int(match.group(1))
        
    return None


def to_anthropic_stream_error(err_data_or_msg, err_type="api_error"):
    """Format stream error response as a valid Anthropic SSE error event."""
    if isinstance(err_data_or_msg, dict):
        msg = err_data_or_msg.get("error", {}).get("message") or err_data_or_msg.get("detail") or str(err_data_or_msg)
    else:
        msg = str(err_data_or_msg)
    return {
        "type": "error",
        "error": {
            "type": err_type,
            "message": msg
        }
    }


def estimate_tokens(body):
    """Estimate the token count of an Anthropic request body."""
    total_chars = 0
    system = body.get("system")
    if system:
        total_chars += len(flatten_content(system))
    for msg in body.get("messages", []):
        content = msg.get("content", "")
        total_chars += len(flatten_content(content))
    tools = body.get("tools")
    if tools:
        total_chars += len(json.dumps(tools))
    # Approximation: 1 token ≈ 3.5 characters for mixed code/natural text
    return max(int(total_chars / 3.5), 1)
