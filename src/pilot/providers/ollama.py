"""Ollama identifies tool results by name; internal IDs remain provider-independent."""

from typing import Any
from uuid import uuid4

import httpx

from pilot.config import OllamaSettings, RetrySettings
from pilot.providers.base import LLMResponse, Message, ProviderError, ToolCall, ToolSpec, Usage
from pilot.providers.retry import post_json


class OllamaProvider:
    def __init__(
        self, client: httpx.Client, settings: OllamaSettings, retry: RetrySettings
    ) -> None:
        self.client = client
        self.settings = settings
        self.retry = retry

    def generate(self, messages: list[Message], tools: list[ToolSpec], **opts: Any) -> LLMResponse:
        wire_messages: list[dict[str, Any]] = []
        calls = {call.id: call for message in messages for call in message.tool_calls}
        for message in messages:
            item: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.tool_calls:
                item["tool_calls"] = [
                    {"function": {"name": call.name, "arguments": call.arguments}}
                    for call in message.tool_calls
                ]
            if message.role == "tool":
                call = calls.get(message.tool_call_id or "")
                if call is None:
                    raise ProviderError("Tool response has no matching call")
                item["tool_name"] = call.name
            item.update(message.provider_data.get("ollama", {}))
            wire_messages.append(item)
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": wire_messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters_json_schema,
                    },
                }
                for tool in tools
            ]
        if opts:
            payload["options"] = opts
        data = post_json(
            self.client, f"{self.settings.host.rstrip('/')}/api/chat", payload, self.retry
        )
        try:
            message = data["message"]
            calls_out = [
                ToolCall(
                    id=call.get("id") or uuid4().hex,
                    name=call["function"]["name"],
                    arguments=call["function"].get("arguments", {}),
                )
                for call in message.get("tool_calls", [])
            ]
            text = message.get("content", "")
            if not text and not calls_out:
                raise ProviderError("Ollama returned no usable answer or tool calls")
            return LLMResponse(
                message=Message(
                    role="assistant",
                    content=text,
                    tool_calls=calls_out,
                    provider_data={"ollama": {"thinking": message["thinking"]}}
                    if "thinking" in message
                    else {},
                ),
                usage=Usage(
                    input_tokens=data.get("prompt_eval_count", 0),
                    output_tokens=data.get("eval_count", 0),
                ),
                raw=data,
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ProviderError("Ollama returned a malformed response") from None
