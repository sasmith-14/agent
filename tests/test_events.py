import json
from pathlib import Path

from pilot.events import EventBus, Redactor, RunStarted, TraceWriter


def test_trace_redacts_secrets_and_writes_one_file_per_run(tmp_path: Path) -> None:
    bus = EventBus()
    writer = TraceWriter(tmp_path, Redactor(['secret"with\\escapes']))
    bus.subscribe(writer)
    for run_id in ("first", "first", "second"):
        bus.emit(
            RunStarted(
                run_id=run_id,
                agent_id="pilot",
                parent_id="parent",
                task='secret"with\\escapes api_key=hidden',
            )
        )
    assert len(list(tmp_path.glob("*.jsonl"))) == 2
    lines = (tmp_path / "first.jsonl").read_text().splitlines()
    assert len(lines) == 2
    event = json.loads(lines[0])
    assert event["parent_id"] == "parent"
    assert event["task"] == "[REDACTED] api_key=[REDACTED]"
    assert event["timestamp"]
    assert (tmp_path / "first.jsonl").stat().st_mode & 0o777 == 0o600


def test_buses_and_subscribers_are_independent() -> None:
    bus, other = EventBus(), EventBus()
    events: list[str] = []

    def collect(event: RunStarted) -> None:
        events.append(event.run_id)
        event.task = "changed"

    bus.subscribe(collect)
    original = RunStarted(run_id="first", agent_id="pilot", task="original")
    other.emit(original)
    assert not events
    bus.emit(original)
    assert original.task == "original"
    bus.unsubscribe(collect)
    bus.emit(original)
    assert events == ["first"]
