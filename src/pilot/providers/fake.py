from collections.abc import Sequence
from typing import Any

from pilot.providers.base import LLMResponse, Message, ProviderError, ToolSpec


class FakeProvider:
    """Each instance owns its script and captured calls for deterministic tests."""

    def __init__(self, responses: Sequence[LLMResponse]) -> None:
        self._responses = iter(responses)
        self.calls: list[list[Message]] = []

    def generate(self, messages: list[Message], tools: list[ToolSpec], **opts: Any) -> LLMResponse:
        self.calls.append([message.model_copy(deep=True) for message in messages])
        try:
            return next(self._responses).model_copy(deep=True)
        except StopIteration:
            raise ProviderError("FakeProvider script exhausted") from None
