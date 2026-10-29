from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Any

from pydantic import ValidationError

from pilot.providers.base import ToolCall, ToolSpec
from pilot.tools.base import Tool, ToolResult


class ToolRegistry:
    """One validation/execution boundary keeps all tool outcomes safe for the loop."""

    def __init__(self, tools: Iterable[Tool[Any]] = (), max_output_length: int = 4000) -> None:
        if max_output_length < 1:
            raise ValueError("max_output_length must be positive")
        self._tools: dict[str, Tool[Any]] = {}
        self.max_output_length = max_output_length
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool[Any]) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any] | None:
        return self._tools.get(name)

    def list(self) -> list[Tool[Any]]:
        return list(self._tools.values())

    def specs(self, allowed_names: Collection[str] | None = None) -> list[ToolSpec]:
        return [
            tool.spec()
            for tool in self._tools.values()
            if allowed_names is None or tool.name in allowed_names
        ]

    def truncate(self, result: ToolResult) -> ToolResult:
        if len(result.output) <= self.max_output_length:
            return result
        suffix = "…[truncated]"[: self.max_output_length]
        return result.model_copy(
            update={"output": result.output[: self.max_output_length - len(suffix)] + suffix}
        )

    def execute(self, call: ToolCall) -> ToolResult:
        tool = self.get(call.name)
        if tool is None:
            return self.truncate(ToolResult(ok=False, output=f"Unknown tool: {call.name}"))
        try:
            args = tool.params_model.model_validate(call.arguments)
            result = tool.run(args)
        except ValidationError as exc:
            errors = "; ".join(
                f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
                for error in exc.errors(include_input=False)
            )
            return self.truncate(ToolResult(ok=False, output=f"Invalid arguments: {errors}"))
        except Exception as exc:
            # Exception text may expose local paths or credentials from future tools.
            result = ToolResult(ok=False, output=f"Tool failed ({type(exc).__name__})")
        return self.truncate(result)
