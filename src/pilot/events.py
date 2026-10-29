"""Synchronous events keep ordering explicit and traces easy to inspect."""

import json
import os
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from pilot.providers.base import Message, ToolCall, ToolSpec, Usage
from pilot.tools.base import Risk, ToolResult

RunStatus = Literal["completed", "limit_reached", "error"]


class BaseEvent(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    run_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    agent_id: str
    parent_id: str | None = None


class RunStarted(BaseEvent):
    type: Literal["run_started"] = "run_started"
    task: str


class LLMRequested(BaseEvent):
    type: Literal["llm_requested"] = "llm_requested"
    step: int
    messages: list[Message]
    tools: list[ToolSpec]


class LLMResponded(BaseEvent):
    type: Literal["llm_responded"] = "llm_responded"
    step: int
    message: Message
    usage: Usage


class ToolRequested(BaseEvent):
    type: Literal["tool_requested"] = "tool_requested"
    call: ToolCall
    risk: Risk


class PolicyDecided(BaseEvent):
    type: Literal["policy_decided"] = "policy_decided"
    tool_call_id: str
    tool_name: str
    policy: Literal["allow", "ask", "deny"]
    allowed: bool


class ToolFinished(BaseEvent):
    type: Literal["tool_finished"] = "tool_finished"
    tool_call_id: str
    tool_name: str
    result: ToolResult


class RunFinished(BaseEvent):
    type: Literal["run_finished"] = "run_finished"
    status: RunStatus
    final_text: str
    steps: int
    usage: Usage


type Event = (
    RunStarted
    | LLMRequested
    | LLMResponded
    | ToolRequested
    | PolicyDecided
    | ToolFinished
    | RunFinished
)
type Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.remove(subscriber)

    def emit(self, event: Event) -> None:
        # A failing trace sink must stop the run before an unrecorded action can happen.
        for subscriber in tuple(self._subscribers):
            subscriber(event.model_copy(deep=True))


class Redactor:
    """Known credential values are masked even when embedded in arbitrary tool text."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets = tuple(sorted((s for s in secrets if s), key=len, reverse=True))

    def text(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"AIza[\w-]{20,}", "[REDACTED]", value)
        return re.sub(
            r"(?i)((?:api[_-]?key|authorization|bearer)[\s\"':=]+)[^\s\",;&}]+",
            r"\1[REDACTED]",
            value,
        )

    def data(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {
                self.text(str(key)): "[REDACTED]"
                if str(key).lower()
                in {"api_key", "apikey", "x-goog-api-key", "authorization", "gemini_api_key"}
                else self.data(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.data(item) for item in value]
        return value


class TraceWriter:
    def __init__(self, runs_dir: Path, redactor: Redactor | None = None) -> None:
        self.runs_dir = runs_dir
        self.redactor = redactor or Redactor()

    def __call__(self, event: Event) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.runs_dir / f"{event.run_id}.jsonl"
        record = self.redactor.data(event.model_dump(mode="json"))
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
