import json
from pathlib import Path

import httpx
import pytest
from rich.console import Console
from typer.testing import CliRunner

from pilot.cli import create_app
from pilot.permissions import ApprovalRequest, CliApprover
from pilot.providers.base import LLMResponse, Message, ToolCall
from pilot.providers.fake import FakeProvider


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text(f"provider: ollama\nollama: {{model: demo}}\nruns_dir: {tmp_path / 'runs'}\n")
    return path


def test_tools_table(config_file: Path) -> None:
    result = CliRunner().invoke(create_app(), ["tools", "--config", str(config_file)])
    assert result.exit_code == 0, result.output
    assert all(name in result.output for name in ("echo", "get_time", "calculator", "ask", "low"))


def test_config_show_masks_secrets(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "private-test-key")
    result = CliRunner().invoke(create_app(), ["config", "show", "--config", str(config_file)])
    assert result.exit_code == 0, result.output
    assert "private-test-key" not in result.output
    data = json.loads(result.output)
    assert data["gemini_api_key"] == "[REDACTED]"
    assert data["provider"] == "ollama"


@pytest.mark.parametrize("approval,allowed", [("y\n", True), ("n\n", False)])
def test_run_interactive_approval(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, approval: str, allowed: bool
) -> None:
    provider = FakeProvider(
        [
            LLMResponse(
                message=Message(
                    role="assistant",
                    tool_calls=[
                        ToolCall(id="1", name="calculator", arguments={"expression": "17*23"})
                    ],
                )
            ),
            LLMResponse(message=Message(role="assistant", content="finished")),
        ]
    )
    monkeypatch.setattr("pilot.cli.create_provider", lambda config, client: provider)
    result = CliRunner().invoke(
        create_app(), ["run", "calculate", "--config", str(config_file)], input=approval
    )
    assert result.exit_code == 0, result.output
    assert "Allow this call?" in result.output
    assert "finished" in result.output and "Tokens:" in result.output
    feedback = json.loads(provider.calls[1][-1].content)
    assert feedback["ok"] is allowed
    assert list((config_file.parent / "runs").glob("*.jsonl"))


def test_invalid_config_is_clear_without_traceback(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("limits: {max_steps: 0}")
    result = CliRunner().invoke(create_app(), ["run", "hello", "--config", str(path)])
    assert result.exit_code == 2
    assert "Invalid configuration" in result.output
    assert "Traceback" not in result.output


def test_run_provider_failure_exits_nonzero(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pilot.cli.create_provider", lambda config, client: FakeProvider([]))
    result = CliRunner().invoke(create_app(), ["run", "task", "--config", str(config_file)])
    assert result.exit_code == 1 and "exhausted" in result.output


def test_approver_eof_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    def eof(*args: object, **kwargs: object) -> bool:
        raise EOFError

    monkeypatch.setattr("pilot.permissions.Confirm.ask", eof)
    assert not CliApprover(Console()).approve(
        ApprovalRequest(
            call=ToolCall(id="1", name="echo", arguments={}),
            risk="low",
            run_id="run",
            agent_id="agent",
        )
    )


def test_cli_with_real_adapter_and_mock_http(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise CLI → real Ollama adapter → both tools without using FakeProvider."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "calculator",
                                    "arguments": {"expression": "17*23"},
                                }
                            },
                            {"function": {"name": "get_time", "arguments": {}}},
                        ],
                    },
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                },
            )
        messages = json.loads(request.content)["messages"]
        assert json.loads(messages[-2]["content"])["output"] == "391"
        assert json.loads(messages[-1]["content"])["ok"]
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "391, now"},
                "prompt_eval_count": 15,
                "eval_count": 3,
            },
        )

    original_client = httpx.Client
    monkeypatch.setattr(
        "pilot.cli.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = CliRunner().invoke(
        create_app(),
        ["run", "What is 17*23 and what time is it?", "--config", str(config_file)],
        input="y\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert "391, now" in result.output and "= 33" in result.output
    assert len(requests) == 2
