"""
OpenAI to Anthropic format converters for OpenChamber support
"""
import json
import time
import uuid
from typing import Dict, List, Any, Optional


def openai_to_anthropic_messages(openai_messages: List[Dict]) -> tuple[Optional[str], List[Dict]]:
    """
    Convert OpenAI chat completion messages to Anthropic format.
    Returns: (system_prompt, anthropic_messages)
    """
    system_prompt = None
    anthropic_messages = []

    for msg in openai_messages:
        role = msg.get("role")
        content = msg.get("content", "")

        if role == "system":
            # Anthropic uses separate system parameter
            if isinstance(content, str):
                system_prompt = content
            elif isinstance(content, list):
                system_prompt = " ".join([
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in content
                ])
            continue

        if role == "assistant":
            # Handle tool calls
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                content_blocks = []
                # Add text content if exists
                if content:
                    content_blocks.append({"type": "text", "text": content})
                # Add tool use blocks
                for tc in tool_calls:
                    func = tc.get("function", {})
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
                        "name": func.get("name", ""),
                        "input": json.loads(func.get("arguments", "{}"))
                    })
                anthropic_messages.append({
                    "role": "assistant",
                    "content": content_blocks
                })
            else:
                # Regular text response
                anthropic_messages.append({
                    "role": "assistant",
                    "content": content if content else ""
                })

        elif role == "tool":
            # Convert tool result to Anthropic format
            anthropic_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": content
                }]
            })

        elif role == "user":
            # Handle user messages with possible images
            if isinstance(content, str):
                anthropic_messages.append({
                    "role": "user",
                    "content": content
                })
            elif isinstance(content, list):
                content_blocks = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            content_blocks.append({"type": "text", "text": block.get("text", "")})
                        elif block.get("type") == "image_url":
                            # Convert OpenAI image_url to Anthropic image format
                            image_url = block.get("image_url", {}).get("url", "")
                            if image_url.startswith("data:"):
                                # Extract base64 data
                                parts = image_url.split(",", 1)
                                if len(parts) == 2:
                                    media_type = parts[0].split(";")[0].replace("data:", "")
                                    content_blocks.append({
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": media_type,
                                            "data": parts[1]
                                        }
                                    })
                    elif isinstance(block, str):
                        content_blocks.append({"type": "text", "text": block})

                anthropic_messages.append({
                    "role": "user",
                    "content": content_blocks if content_blocks else ""
                })

    return system_prompt, anthropic_messages


def _created_timestamp() -> int:
    """Return a valid OpenAI-style Unix timestamp."""
    return int(time.time())


def anthropic_to_openai_response(anthropic_response: Dict, model: str) -> Dict:
    """
    Convert Anthropic response to OpenAI chat completion format.
    """
    content_blocks = anthropic_response.get("content", [])

    # Build message content
    message_content = ""
    tool_calls = []

    for block in content_blocks:
        if isinstance(block, dict):
            if block.get("type") == "text":
                message_content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {}))
                    }
                })

    # Build message object
    message = {
        "role": "assistant",
        "content": message_content or None
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    # Build usage
    usage_data = anthropic_response.get("usage", {})
    usage = {
        "prompt_tokens": usage_data.get("input_tokens", 0),
        "completion_tokens": usage_data.get("output_tokens", 0),
        "total_tokens": usage_data.get("input_tokens", 0) + usage_data.get("output_tokens", 0)
    }

    # Build full response
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:28]}",
        "object": "chat.completion",
        "created": _created_timestamp(),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": _map_stop_reason(anthropic_response.get("stop_reason"))
        }],
        "usage": usage
    }


def anthropic_to_openai_stream_chunk(chunk_data: Dict, model: str) -> Optional[str]:
    """
    Convert Anthropic SSE chunk to OpenAI format.
    Returns formatted SSE line or None if not a content chunk.
    """
    event_type = chunk_data.get("type")

    if event_type == "message_start":
        # Initial chunk
        message_data = chunk_data.get("message", {})
        chunk = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:28]}",
            "object": "chat.completion.chunk",
            "created": _created_timestamp(),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": None
            }]
        }
        return f"data: {json.dumps(chunk)}\n\n"

    elif event_type == "content_block_delta":
        delta = chunk_data.get("delta", {})
        delta_type = delta.get("type")

        if delta_type == "text_delta":
            chunk = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:28]}",
                "object": "chat.completion.chunk",
                "created": _created_timestamp(),
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": delta.get("text", "")},
                    "finish_reason": None
                }]
            }
            return f"data: {json.dumps(chunk)}\n\n"

    elif event_type == "message_delta":
        delta = chunk_data.get("delta", {})
        stop_reason = delta.get("stop_reason")
        if stop_reason:
            chunk = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:28]}",
                "object": "chat.completion.chunk",
                "created": _created_timestamp(),
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": _map_stop_reason(stop_reason)
                }]
            }
            return f"data: {json.dumps(chunk)}\n\n"

    elif event_type == "message_stop":
        # The caller closes the stream with its own [DONE]; emitting one here
        # too put two of them on the wire.
        return None

    return None


def _map_stop_reason(anthropic_stop_reason: Optional[str]) -> Optional[str]:
    """Map Anthropic stop_reason to OpenAI finish_reason."""
    if not anthropic_stop_reason:
        return None

    mapping = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "tool_use": "tool_calls"
    }
    return mapping.get(anthropic_stop_reason, "stop")
