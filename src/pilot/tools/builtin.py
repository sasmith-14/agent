"""The calculator accepts arithmetic syntax, never executable Python."""

import ast
import math
from datetime import datetime

from pydantic import Field

from pilot.tools.base import Tool, ToolArgs, ToolResult
from pilot.tools.registry import ToolRegistry


class EchoArgs(ToolArgs):
    text: str


class EchoTool(Tool[EchoArgs]):
    name = "echo"
    description = "Repeat text unchanged."
    params_model = EchoArgs
    risk = "low"

    def run(self, args: EchoArgs) -> ToolResult:
        return ToolResult(ok=True, output=args.text)


class TimeArgs(ToolArgs):
    pass


class GetTimeTool(Tool[TimeArgs]):
    name = "get_time"
    description = "Get the current local date and time, including the UTC offset."
    params_model = TimeArgs
    risk = "low"

    def run(self, args: TimeArgs) -> ToolResult:
        return ToolResult(ok=True, output=datetime.now().astimezone().isoformat())


class CalculatorArgs(ToolArgs):
    expression: str = Field(
        min_length=1,
        max_length=200,
        description="Arithmetic using numbers, parentheses, + - * / // %.",
    )


def _evaluate(node: ast.AST) -> int | float:
    match node:
        case ast.Constant(value=value) if type(value) in (int, float):
            result = value
        case ast.UnaryOp(op=ast.UAdd(), operand=operand):
            result = _evaluate(operand)
        case ast.UnaryOp(op=ast.USub(), operand=operand):
            result = -_evaluate(operand)
        case ast.BinOp(left=left, op=op, right=right):
            a, b = _evaluate(left), _evaluate(right)
            match op:
                case ast.Add():
                    result = a + b
                case ast.Sub():
                    result = a - b
                case ast.Mult():
                    result = a * b
                case ast.Div():
                    result = a / b
                case ast.FloorDiv():
                    result = a // b
                case ast.Mod():
                    result = a % b
                case _:
                    raise ValueError("Unsupported operator")
        case _:
            raise ValueError("Only numeric arithmetic is supported")
    if not math.isfinite(result) or abs(result) > 1e100:
        raise ValueError("Arithmetic result is too large")
    return result


class CalculatorTool(Tool[CalculatorArgs]):
    name = "calculator"
    description = "Calculate exact arithmetic; use this for arithmetic questions."
    params_model = CalculatorArgs
    risk = "low"

    def run(self, args: CalculatorArgs) -> ToolResult:
        try:
            tree = ast.parse(args.expression.strip(), mode="eval")
            if sum(1 for _ in ast.walk(tree)) > 100:
                raise ValueError("Expression is too complex")
            return ToolResult(ok=True, output=str(_evaluate(tree.body)))
        except (ValueError, SyntaxError, ArithmeticError) as exc:
            return ToolResult(ok=False, output=f"Invalid arithmetic: {exc}")


def builtin_registry(max_output_length: int = 4000) -> ToolRegistry:
    return ToolRegistry([EchoTool(), GetTimeTool(), CalculatorTool()], max_output_length)
