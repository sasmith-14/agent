"""Only normalized values cross the provider boundary."""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class ProviderError(RuntimeError):
    """Safe to display: implementations must not include credentials or response bodies."""


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    # Opaque round-trip data preserves signatures without teaching the loop a wire format.
    provider_data: dict[str, Any] = Field(default_factory=dict, exclude=True)


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters_json_schema: dict[str, Any]


class Usage(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMResponse(BaseModel):
    message: Message
    usage: Usage = Field(default_factory=Usage)
    raw: dict[str, Any] | None = None


class LLMProvider(Protocol):
    def generate(
        self, messages: list[Message], tools: list[ToolSpec], **opts: Any
    ) -> LLMResponse: ...
