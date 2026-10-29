"""generateContent REST mapping; signatures and original parts must survive tool turns.

Reference: https://ai.google.dev/gemini-api/docs/generate-content/function-calling?hl=en
"""

from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from pilot.config import GeminiSettings, RetrySettings
from pilot.providers.base import LLMResponse, Message, ProviderError, ToolCall, ToolSpec, Usage
from pilot.providers.retry import post_json


class GeminiProvider:
    def __init__(
        self, client: httpx.Client, settings: GeminiSettings, api_key: str, retry: RetrySettings
    ) -> None:
        self.client = client
        self.settings = settings
        self.api_key = api_key
        self.retry = retry

    def _contents(self, messages: list[Message]) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        calls = {call.id: call for message in messages for call in message.tool_calls}
        for message in messages:
            if message.role == "system":
                continue
            if message.role == "tool":
                call = calls.get(message.tool_call_id or "")
                if call is None:
                    raise ProviderError("Tool response has no matching call")
                result: dict[str, Any] = {
                    "name": call.name,
                    "response": {"result": message.content},
                }
                # Older models omit IDs; generated internal IDs need not go onto the wire.
                wire_ids = {
                    part["functionCall"].get("id")
                    for item in messages
                    for part in item.provider_data.get("gemini_content", {}).get("parts", [])
                    if "functionCall" in part
                }
                if call.id in wire_ids:
                    result["id"] = call.id
                part = {"functionResponse": result}
                if (
                    contents
                    and contents[-1]["role"] == "user"
                    and all("functionResponse" in item for item in contents[-1]["parts"])
                ):
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
            elif message.role == "assistant" and "gemini_content" in message.provider_data:
                contents.append(message.provider_data["gemini_content"])
            else:
                parts: list[dict[str, Any]] = []
                if message.content:
                    parts.append({"text": message.content})
                parts.extend(
                    {"functionCall": {"name": call.name, "args": call.arguments}}
                    for call in message.tool_calls
                )
                contents.append(
                    {"role": "model" if message.role == "assistant" else "user", "parts": parts}
                )
        return contents

    def generate(self, messages: list[Message], tools: list[ToolSpec], **opts: Any) -> LLMResponse:
        if not self.api_key:
            raise ProviderError("Set GEMINI_API_KEY in the environment or .env")
        payload: dict[str, Any] = {"contents": self._contents(messages)}
        system = "\n\n".join(message.content for message in messages if message.role == "system")
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parametersJsonSchema": tool.parameters_json_schema,
                        }
                        for tool in tools
                    ]
                }
            ]
        if opts:
            payload["generationConfig"] = opts
        model = quote(self.settings.model.removeprefix("models/"), safe="")
        data = post_json(
            self.client,
            f"{self.settings.base_url.rstrip('/')}/models/{model}:generateContent",
            payload,
            self.retry,
            headers={"x-goog-api-key": self.api_key},
        )
        try:
            candidate = data["candidates"][0]
            content = candidate["content"]
            parts = content["parts"]
            calls = [
                ToolCall(
                    id=part["functionCall"].get("id") or uuid4().hex,
                    name=part["functionCall"]["name"],
                    arguments=part["functionCall"].get("args", {}),
                )
                for part in parts
                if "functionCall" in part
            ]
            text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
            if not text and not calls:
                raise ProviderError("Gemini returned no usable answer or tool calls")
            counts = data.get("usageMetadata", {})
            inputs = counts.get("promptTokenCount", 0) + counts.get("toolUsePromptTokenCount", 0)
            outputs = counts.get("candidatesTokenCount", 0) + counts.get("thoughtsTokenCount", 0)
            outputs = max(outputs, counts.get("totalTokenCount", 0) - inputs)
            return LLMResponse(
                message=Message(
                    role="assistant",
                    content=text,
                    tool_calls=calls,
                    provider_data={"gemini_content": content},
                ),
                usage=Usage(input_tokens=inputs, output_tokens=outputs),
                raw=data,
            )
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise ProviderError("Gemini returned a blocked or malformed response") from None
