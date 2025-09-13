"""
Utility helpers for Anthropic <-> OpenAI conversions.
Extracted from converter classes for reuse.
"""
from typing import Dict, Any
import json
import time
from .base_converter import ConversionError
from .reasoning_utils import determine_reasoning_effort

def anthropic_request_to_openai(converter, data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an Anthropic request to an OpenAI Chat Completions request."""
    result_data: Dict[str, Any] = {}

    # Model is passed through directly
    if "model" in data:
        result_data["model"] = data["model"]

    messages = []
    if "system" in data:
        messages.append(converter._create_system_message(data["system"]))

    if "messages" in data:
        for msg in data["messages"]:
            role = msg.get("role")
            if role in ["user", "assistant"]:
                if role == "user" and isinstance(msg.get("content"), list):
                    has_tool_result = any(
                        isinstance(item, dict) and item.get("type") == "tool_result"
                        for item in msg["content"]
                    )
                    if has_tool_result:
                        for item in msg["content"]:
                            if isinstance(item, dict) and item.get("type") == "tool_result":
                                tool_use_id = (
                                    item.get("tool_use_id")
                                    or item.get("id")
                                    or ""
                                )
                                content_str = str(item.get("content", ""))
                                messages.append(
                                    {
                                        "role": "tool",
                                        "tool_call_id": tool_use_id,
                                        "content": content_str,
                                    }
                                )
                        continue
                if role == "assistant" and isinstance(msg.get("content"), list) and msg["content"]:
                    first_part = msg["content"][0]
                    if first_part.get("type") == "tool_use":
                        func_name = first_part.get("name", "")
                        func_args = first_part.get("input", {})
                        messages.append(
                            {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": first_part.get("id")
                                        or first_part.get("tool_use_id")
                                        or f"call_{func_name}_1",
                                        "type": "function",
                                        "function": {
                                            "name": func_name,
                                            "arguments": json.dumps(
                                                func_args, ensure_ascii=False
                                            ),
                                        },
                                    }
                                ],
                            }
                        )
                        continue
                content_converted = converter._convert_content_from_anthropic(
                    msg.get("content", "")
                )
                if not content_converted:
                    continue
                messages.append({"role": role, "content": content_converted})
    result_data["messages"] = messages

    # Validate assistant tool_calls have matching tool responses
    validated_messages = []
    for idx, m in enumerate(messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            call_ids = [tc.get("id") for tc in m["tool_calls"] if tc.get("id")]
            unmatched = set(call_ids)
            for later in messages[idx + 1:]:
                if later.get("role") == "tool" and later.get("tool_call_id") in unmatched:
                    unmatched.discard(later["tool_call_id"])
                if not unmatched:
                    break
            if unmatched:
                m["tool_calls"] = [tc for tc in m["tool_calls"] if tc.get("id") not in unmatched]
                if not m["tool_calls"]:
                    m.pop("tool_calls", None)
                    if m.get("content") is None:
                        m["content"] = ""
        validated_messages.append(m)
    result_data["messages"] = validated_messages

    if "max_tokens" in data:
        result_data["max_tokens"] = data["max_tokens"]
    if "temperature" in data:
        result_data["temperature"] = data["temperature"]
    if "top_p" in data:
        result_data["top_p"] = data["top_p"]
    if "top_k" in data:
        pass
    if "stop_sequences" in data:
        result_data["stop"] = data["stop_sequences"]
    if "stream" in data:
        result_data["stream"] = data["stream"]

    if "tools" in data:
        openai_tools = []
        for tool in data["tools"]:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": converter._clean_json_schema_properties(
                            tool.get("input_schema", {})
                        ),
                    },
                }
            )
        result_data["tools"] = openai_tools
        result_data["tool_choice"] = "auto"

    if "thinking" in data and data["thinking"].get("type") == "enabled":
        budget_tokens = data["thinking"].get("budget_tokens")
        reasoning_effort = determine_reasoning_effort(
            budget_tokens,
            "ANTHROPIC_TO_OPENAI_LOW_REASONING_THRESHOLD",
            "ANTHROPIC_TO_OPENAI_HIGH_REASONING_THRESHOLD",
            converter.logger,
            budget_label="Budget tokens",
        )
        result_data["reasoning_effort"] = reasoning_effort

        max_completion_tokens = None
        if "max_tokens" in data:
            max_completion_tokens = data["max_tokens"]
            result_data.pop("max_tokens", None)
            converter.logger.info(
                f"Using client max_tokens as max_completion_tokens: {max_completion_tokens}"
            )
        else:
            import os
            env_max_tokens = os.environ.get("OPENAI_REASONING_MAX_TOKENS")
            if env_max_tokens:
                try:
                    max_completion_tokens = int(env_max_tokens)
                    converter.logger.info(
                        f"Using OPENAI_REASONING_MAX_TOKENS from environment: {max_completion_tokens}"
                    )
                except ValueError:
                    converter.logger.warning(
                        f"Invalid OPENAI_REASONING_MAX_TOKENS value '{env_max_tokens}', must be integer"
                    )
                    env_max_tokens = None
            if not env_max_tokens:
                raise ConversionError(
                    "For OpenAI reasoning models, max_completion_tokens is required. Please specify max_tokens in the request or set OPENAI_REASONING_MAX_TOKENS environment variable."
                )
        result_data["max_completion_tokens"] = max_completion_tokens
        converter.logger.info(
            f"Anthropic thinking enabled -> OpenAI reasoning_effort='{reasoning_effort}', max_completion_tokens={max_completion_tokens}"
        )
        if budget_tokens:
            converter.logger.info(
                f"Budget tokens: {budget_tokens} -> reasoning_effort: '{reasoning_effort}'"
            )

    return result_data


def anthropic_response_to_openai(converter, data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an Anthropic response to an OpenAI Chat Completions response."""
    if not getattr(converter, "original_model", None):
        raise ValueError("Original model name is required for response conversion")

    result_data = {
        "id": f"chatcmpl-{data.get('id', 'anthropic')}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": converter.original_model,
        "choices": [],
        "usage": {},
    }

    content = ""
    tool_calls = []
    thinking_content = ""

    if "content" in data and isinstance(data["content"], list):
        for item in data["content"]:
            if item.get("type") == "text":
                content += item.get("text", "")
            elif item.get("type") == "thinking":
                thinking_text = item.get("thinking", "")
                if thinking_text.strip():
                    thinking_content += thinking_text
            elif item.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": item.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": item.get("name", ""),
                            "arguments": json.dumps(
                                item.get("input", {}), ensure_ascii=False
                            ),
                        },
                    }
                )

    if thinking_content.strip():
        content = f"<thinking>\n{thinking_content.strip()}\n</thinking>\n\n{content}"

    message = {"role": "assistant"}
    if tool_calls:
        message["content"] = content if content else None
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"
    else:
        message["content"] = content
        finish_reason = converter._map_finish_reason(
            data.get("stop_reason", ""), "anthropic", "openai"
        )

    result_data["choices"] = [
        {"index": 0, "message": message, "finish_reason": finish_reason}
    ]

    if "usage" in data and data["usage"] is not None:
        usage = data["usage"]
        result_data["usage"] = {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0)
            + usage.get("output_tokens", 0),
        }

    return result_data
