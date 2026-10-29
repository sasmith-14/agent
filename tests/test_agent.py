import json
from pathlib import Path

import pytest

from pilot.agent.loop import Agent
from pilot.config import Limits, Policy, PolicySettings
from pilot.events import Event, EventBus, TraceWriter
from pilot.permissions import AutoApprover, AutoDenier, PolicyEngine
from pilot.providers.base import LLMResponse, Message, ToolCall, Usage
from pilot.providers.fake import FakeProvider
from pilot.tools.base import ToolResult
from pilot.tools.builtin import EchoArgs, EchoTool, builtin_registry
from pilot.tools.registry import ToolRegistry


def answer(text: str = "done", tokens: int = 2) -> LLMResponse:
    return LLMResponse(
        message=Message(role="assistant", content=text),
        usage=Usage(input_tokens=tokens, output_tokens=1),
    )


def tool_response(name: str = "echo", arguments: dict[str, object] | None = None) -> LLMResponse:
    return LLMResponse(
        message=Message(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="call-1",
                    name=name,
                    arguments={"text": "hello"} if arguments is None else arguments,
                )
            ],
        ),
        usage=Usage(input_tokens=2, output_tokens=1),
    )


def make_agent(
    provider: FakeProvider,
    *,
    policy: Policy = "allow",
    approved: bool = True,
    limits: Limits | None = None,
    bus: EventBus | None = None,
    tools: ToolRegistry | None = None,
) -> Agent:
    return Agent(
        "pilot",
        provider,
        tools if tools is not None else builtin_registry(),
        PolicyEngine(PolicySettings(default=policy), AutoApprover() if approved else AutoDenier()),
        bus or EventBus(),
        "help",
        limits or Limits(),
        parent_id="parent",
    )


def test_single_turn() -> None:
    provider = FakeProvider([answer("hello")])
    result = make_agent(provider).run("task")
    assert result.status == "completed" and result.final_text == "hello"
    assert result.steps == 1 and result.usage.total_tokens == 3
    assert [message.role for message in provider.calls[0]] == ["system", "user"]


def test_tool_then_answer_and_all_trace_event_types(tmp_path: Path) -> None:
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe(events.append)
    bus.subscribe(TraceWriter(tmp_path))
    provider = FakeProvider([tool_response(), answer()])
    result = make_agent(provider, bus=bus).run("echo")
    assert result.status == "completed" and result.steps == 2
    tool_message = provider.calls[1][-1]
    assert tool_message.tool_call_id == "call-1"
    assert json.loads(tool_message.content) == {"ok": True, "output": "hello"}
    expected = [
        "run_started",
        "llm_requested",
        "llm_responded",
        "tool_requested",
        "policy_decided",
        "tool_finished",
        "llm_requested",
        "llm_responded",
        "run_finished",
    ]
    assert [event.type for event in events] == expected
    records = [
        json.loads(line) for line in (tmp_path / f"{result.run_id}.jsonl").read_text().splitlines()
    ]
    assert [record["type"] for record in records] == expected
    assert all(
        record["agent_id"] == "pilot"
        and record["parent_id"] == "parent"
        and record["run_id"] == result.run_id
        for record in records
    )


@pytest.mark.parametrize(
    "name,args,output",
    [
        ("missing", {}, "Unknown tool"),
        ("echo", {}, "Invalid arguments"),
        ("echo", {"text": 1}, "Invalid arguments"),
    ],
)
def test_tool_errors_return_to_model(name: str, args: dict[str, object], output: str) -> None:
    provider = FakeProvider([tool_response(name, args), answer()])
    assert make_agent(provider).run("task").status == "completed"
    result = json.loads(provider.calls[1][-1].content)
    assert result["ok"] is False and output in result["output"]


@pytest.mark.parametrize(
    "policy,approved,allowed",
    [
        ("deny", True, False),
        ("allow", False, True),
        ("ask", True, True),
        ("ask", False, False),
    ],
)
def test_policy_feedback(policy: Policy, approved: bool, allowed: bool) -> None:
    provider = FakeProvider([tool_response(), answer()])
    make_agent(provider, policy=policy, approved=approved).run("task")
    result = json.loads(provider.calls[1][-1].content)
    assert result["ok"] is allowed
    if not allowed:
        assert result["output"] == "Denied by policy"


def test_max_steps() -> None:
    provider = FakeProvider([tool_response(), answer()])
    result = make_agent(provider, limits=Limits(max_steps=1)).run("task")
    assert result.status == "limit_reached" and result.steps == 1
    assert len(provider.calls) == 1


def test_tokens_stop_before_tool_execution() -> None:
    provider = FakeProvider([tool_response(), answer()])
    events: list[Event] = []
    bus = EventBus()
    bus.subscribe(events.append)
    result = make_agent(provider, bus=bus, limits=Limits(max_total_tokens=3)).run("task")
    assert result.status == "limit_reached" and result.usage.total_tokens == 3
    assert not any(event.type == "tool_requested" for event in events)
    assert events[-1].type == "run_finished"


def test_token_usage_accumulates_across_steps() -> None:
    provider = FakeProvider([tool_response(), answer(tokens=10)])
    result = make_agent(provider, limits=Limits(max_total_tokens=12)).run("task")
    assert result.status == "limit_reached" and result.usage.total_tokens == 14


def test_tool_exception_returns_error_to_model() -> None:
    class BrokenEcho(EchoTool):
        def run(self, args: EchoArgs) -> ToolResult:
            raise RuntimeError("private details")

    provider = FakeProvider([tool_response(), answer()])
    result = make_agent(provider, tools=ToolRegistry([BrokenEcho()])).run("task")
    assert result.status == "completed"
    assert json.loads(provider.calls[1][-1].content) == {
        "ok": False,
        "output": "Tool failed (RuntimeError)",
    }


def test_sequential_calls() -> None:
    response = tool_response()
    response.message.tool_calls.append(
        ToolCall(id="call-2", name="calculator", arguments={"expression": "17*23"})
    )
    provider = FakeProvider([response, answer()])
    make_agent(provider).run("task")
    results = provider.calls[1][-2:]
    assert [message.tool_call_id for message in results] == ["call-1", "call-2"]
    assert json.loads(results[1].content)["output"] == "391"


def test_provider_failure_finishes_trace() -> None:
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe(events.append)
    result = make_agent(FakeProvider([]), bus=bus).run("task")
    assert result.status == "error" and "exhausted" in result.final_text
    assert events[-1].type == "run_finished"


def test_two_agents_and_repeated_runs_do_not_share_state() -> None:
    first_provider, second_provider = FakeProvider([answer(), answer()]), FakeProvider([answer()])
    first = make_agent(first_provider)
    second = make_agent(second_provider, tools=ToolRegistry())
    second.system_prompt = "other prompt"
    first_events: list[Event] = []
    first.bus.subscribe(first_events.append)
    a = first.run("one")
    b = second.run("two")
    c = first.run("three")
    assert len({a.run_id, b.run_id, c.run_id}) == 3
    assert len(first.tools.list()) == 3 and second.tools.list() == []
    assert first_provider.calls[1][1].content == "three"
    assert len(first_provider.calls[1]) == 2
    assert first_provider.calls[0][0].content == "help"
    assert second_provider.calls[0][0].content == "other prompt"
    assert {event.run_id for event in first_events} == {a.run_id, c.run_id}
