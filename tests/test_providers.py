import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

from pilot.config import GeminiSettings, OllamaSettings, RetrySettings
from pilot.providers.base import Message, ProviderError, ToolSpec
from pilot.providers.gemini import GeminiProvider
from pilot.providers.ollama import OllamaProvider
from pilot.providers.retry import post_json


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retry_status_and_jitter(status: int) -> None:
    statuses: Iterator[int] = iter([status, status, 200])
    sleeps: list[float] = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(next(statuses), json={"ok": True})
        )
    ) as client:
        assert post_json(
            client, "https://test/", {}, RetrySettings(), sleep=sleeps.append, jitter=lambda: 0.5
        ) == {"ok": True}
    assert sleeps == [0.75, 1.5]


@pytest.mark.parametrize("header", ["3", "date"])
def test_retry_after(header: str) -> None:
    if header == "date":
        header = format_datetime(datetime.now(UTC) + timedelta(seconds=4))
    statuses = iter([429, 200])
    sleeps: list[float] = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(next(statuses), headers={"Retry-After": header}, json={})
        )
    ) as client:
        post_json(
            client, "https://test/", {}, RetrySettings(), sleep=sleeps.append, jitter=lambda: 0
        )
    assert 2.5 <= sleeps[0] <= 4


def test_retry_timeout_exhaustion() -> None:
    attempts = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("secret", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(ProviderError, match="3 attempts.*timed out"):
            post_json(client, "https://test/", {}, RetrySettings(), sleep=lambda _: None)
    assert attempts == 3


@pytest.mark.parametrize("status", [400, 401, 403])
def test_permanent_errors_are_not_retried(status: int) -> None:
    attempts = 0

    def reject(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status, text="secret-key")

    with httpx.Client(transport=httpx.MockTransport(reject)) as client:
        with pytest.raises(ProviderError, match=f"HTTP {status}") as error:
            post_json(client, "https://test/?key=secret-key", {}, RetrySettings())
    assert "secret-key" not in str(error.value)
    assert attempts == 1


def test_retry_after_beyond_bound_aborts_without_early_retry() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "1000"})
        )
    ) as client:
        with pytest.raises(ProviderError, match="exceeds"):
            post_json(
                client,
                "https://test/",
                {},
                RetrySettings(),
                sleep=lambda _: pytest.fail("Should not sleep"),
            )


def test_gemini_tool_round_trip_preserves_signatures_and_ids() -> None:
    requests: list[dict[str, Any]] = []
    original = {
        "role": "model",
        "parts": [
            {
                "functionCall": {"name": "echo", "args": {"text": "hello"}, "id": "api-id"},
                "thoughtSignature": "opaque-signature",
            },
            {"functionCall": {"name": "echo", "args": {"text": "again"}}},
        ],
    }

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        assert request.headers["x-goog-api-key"] == "test-key"
        assert request.url.path == "/v1beta/models/configured-model:generateContent"
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "candidates": [{"content": original}],
                    "usageMetadata": {
                        "promptTokenCount": 3,
                        "candidatesTokenCount": 2,
                        "thoughtsTokenCount": 4,
                        "totalTokenCount": 9,
                    },
                },
            )
        return httpx.Response(
            200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": "done"}]}}]}
        )

    spec = ToolSpec(name="echo", description="repeat", parameters_json_schema={"type": "object"})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider = GeminiProvider(
            client, GeminiSettings(model="configured-model"), "test-key", RetrySettings()
        )
        messages = [Message(role="system", content="help"), Message(role="user", content="echo")]
        first = provider.generate(messages, [spec])
        assert first.usage.total_tokens == 9
        assert first.message.tool_calls[0].id == "api-id"
        assert first.message.tool_calls[1].id
        messages.append(first.message)
        messages.extend(
            Message(role="tool", content="result", tool_call_id=call.id)
            for call in first.message.tool_calls
        )
        assert provider.generate(messages, [spec]).message.content == "done"
    sent = requests[1]
    assert sent["contents"][1] == original
    results = sent["contents"][2]["parts"]
    assert len(results) == 2
    assert results[0]["functionResponse"]["id"] == "api-id"
    assert "id" not in results[1]["functionResponse"]
    assert sent["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"] == {"type": "object"}
    assert sent["systemInstruction"]["parts"][0]["text"] == "help"


def test_ollama_tool_round_trip() -> None:
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        assert request.url.path == "/api/chat"
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "thinking": "opaque",
                        "tool_calls": [
                            {"function": {"name": "echo", "arguments": {"text": "hello"}}}
                        ],
                    },
                    "prompt_eval_count": 5,
                    "eval_count": 3,
                },
            )
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "done"}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider = OllamaProvider(client, OllamaSettings(model="configured"), RetrySettings())
        messages = [Message(role="user", content="hello")]
        first = provider.generate(messages, [])
        assert first.usage.total_tokens == 8
        messages.extend(
            [
                first.message,
                Message(role="tool", content="hello", tool_call_id=first.message.tool_calls[0].id),
            ]
        )
        assert provider.generate(messages, []).message.content == "done"
    assert requests[0]["stream"] is False
    assert requests[1]["messages"][-1]["tool_name"] == "echo"
    assert requests[1]["messages"][1]["thinking"] == "opaque"


@pytest.mark.parametrize("provider_name", ["gemini", "ollama"])
@pytest.mark.parametrize("data", [{}, {"candidates": []}, {"message": None}])
def test_malformed_provider_response(provider_name: str, data: dict[str, Any]) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))
    ) as client:
        provider = (
            GeminiProvider(client, GeminiSettings(model="demo"), "key", RetrySettings())
            if provider_name == "gemini"
            else OllamaProvider(client, OllamaSettings(model="demo"), RetrySettings())
        )
        with pytest.raises(ProviderError):
            provider.generate([Message(role="user", content="hello")], [])
