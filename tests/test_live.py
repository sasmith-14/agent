"""Explicitly opt in; these tests never run in the default offline suite."""

import os

import httpx
import pytest

from pilot.agent.loop import Agent
from pilot.agent.prompts import DEFAULT_SYSTEM_PROMPT
from pilot.config import Config, Limits, PolicySettings
from pilot.events import Event, EventBus
from pilot.permissions import AutoApprover, PolicyEngine
from pilot.providers import create_provider
from pilot.tools.builtin import builtin_registry


def smoke(config: Config) -> None:
    events: list[Event] = []
    bus = EventBus()
    bus.subscribe(events.append)
    with httpx.Client(timeout=config.timeout_seconds) as client:
        result = Agent(
            "smoke",
            create_provider(config, client),
            builtin_registry(),
            PolicyEngine(PolicySettings(default="allow"), AutoApprover()),
            bus,
            DEFAULT_SYSTEM_PROMPT,
            Limits(max_steps=6),
        ).run(
            "Use calculator for 17*23 AND get_time for the current local time. "
            "Then report both results."
        )
    assert result.status == "completed", result.final_text
    assert "391" in result.final_text
    completed = {
        event.tool_name for event in events if event.type == "tool_finished" and event.result.ok
    }
    assert {"calculator", "get_time"} <= completed


@pytest.mark.live
def test_live_gemini() -> None:
    if not os.environ.get("GEMINI_API_KEY") or not os.environ.get("PILOT_GEMINI__MODEL"):
        pytest.skip("Set GEMINI_API_KEY and PILOT_GEMINI__MODEL")
    smoke(Config(provider="gemini"))


@pytest.mark.live
def test_live_ollama() -> None:
    if not os.environ.get("PILOT_OLLAMA__MODEL"):
        pytest.skip("Set PILOT_OLLAMA__MODEL; a running Ollama host is required")
    smoke(Config(provider="ollama"))
