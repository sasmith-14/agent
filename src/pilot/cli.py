"""Construct the CLI per invocation so agents and consoles never share mutable globals."""

import json
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

from pilot.agent.loop import Agent
from pilot.agent.prompts import DEFAULT_SYSTEM_PROMPT
from pilot.config import Config, ConfigError, load_config
from pilot.events import Event, EventBus, Redactor, TraceWriter
from pilot.permissions import CliApprover, PolicyEngine
from pilot.providers import create_provider
from pilot.tools.builtin import builtin_registry


class StepView:
    def __init__(self, console: Console, redactor: Redactor) -> None:
        self.console = console
        self.redactor = redactor

    def __call__(self, event: Event) -> None:
        match event.type:
            case "run_started":
                text = f"Run {event.run_id}"
            case "llm_requested":
                text = f"Step {event.step}: asking provider"
            case "llm_responded":
                text = (
                    f"Step {event.step}: received {len(event.message.tool_calls)} tool calls "
                    f"({event.usage.total_tokens} tokens)"
                )
            case "tool_requested":
                text = f"→ {event.call.name} {json.dumps(event.call.arguments)}"
            case "policy_decided":
                text = (
                    f"  Policy {event.policy}: "
                    f"{'approved' if event.allowed else 'denied'} {event.tool_name}"
                )
            case "tool_finished":
                text = f"  {'OK' if event.result.ok else 'Error'}: {event.result.output}"
            case "run_finished":
                text = f"Run {event.status}"
        self.console.print(self.redactor.text(text), markup=False, highlight=False)


def _config(console: Console, path: Path | None, provider: str | None = None) -> Config:
    try:
        return load_config(path, provider)
    except ConfigError as exc:
        console.print(str(exc), style="red", markup=False)
        raise typer.Exit(2) from None


def create_app() -> typer.Typer:
    app = typer.Typer(help="Pilot: a small, observable local agent.", no_args_is_help=True)
    config_app = typer.Typer(help="Inspect effective configuration.")
    app.add_typer(config_app, name="config")

    @app.command()
    def run(
        task: Annotated[str, typer.Argument(help="The task to carry out.")],
        provider: Annotated[str | None, typer.Option(help="gemini or ollama")] = None,
        config: Annotated[Path | None, typer.Option(help="YAML configuration path")] = None,
    ) -> None:
        console = Console()
        settings = _config(console, config, provider)
        redactor = Redactor([settings.gemini_api_key.get_secret_value()])
        bus = EventBus()
        # Persist each event before displaying it or performing the next action.
        bus.subscribe(TraceWriter(settings.runs_dir, redactor))
        bus.subscribe(StepView(console, redactor))
        try:
            with httpx.Client(timeout=settings.timeout_seconds) as client:
                agent = Agent(
                    "pilot",
                    create_provider(settings, client),
                    builtin_registry(settings.tool_output_max_length),
                    PolicyEngine(settings.policy, CliApprover(console, redactor)),
                    bus,
                    DEFAULT_SYSTEM_PROMPT,
                    settings.limits,
                )
                result = agent.run(task)
        except OSError:
            console.print("Could not write the run trace; check runs_dir permissions.", style="red")
            raise typer.Exit(1) from None
        console.print(redactor.text(result.final_text), markup=False, highlight=False)
        console.print(
            f"Tokens: {result.usage.input_tokens} input + {result.usage.output_tokens} output "
            f"= {result.usage.total_tokens} | Steps: {result.steps}",
            markup=False,
        )
        if result.status != "completed":
            raise typer.Exit(1)

    @app.command()
    def tools(
        config: Annotated[Path | None, typer.Option(help="YAML configuration path")] = None,
    ) -> None:
        console = Console()
        settings = _config(console, config)
        policy = PolicyEngine(settings.policy, CliApprover(console))
        table = Table("Tool", "Description", "Risk", "Policy")
        for tool in builtin_registry().list():
            table.add_row(
                tool.name, tool.description, tool.risk, policy.effective_policy(tool.name)
            )
        console.print(table)

    @config_app.command("show")
    def show_config(
        config: Annotated[Path | None, typer.Option(help="YAML configuration path")] = None,
    ) -> None:
        console = Console()
        settings = _config(console, config)
        redactor = Redactor([settings.gemini_api_key.get_secret_value()])
        data = settings.model_dump(mode="json")
        console.print_json(data=redactor.data(data))

    return app


def app() -> None:
    create_app()()


if __name__ == "__main__":
    app()
