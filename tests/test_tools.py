import pytest

from pilot.providers.base import ToolCall
from pilot.tools.builtin import EchoTool, builtin_registry
from pilot.tools.registry import ToolRegistry


def test_schema_and_subsets() -> None:
    registry = builtin_registry()
    specs = registry.specs({"echo"})
    assert [spec.name for spec in specs] == ["echo"]
    assert specs[0].parameters_json_schema["required"] == ["text"]
    assert specs[0].parameters_json_schema["additionalProperties"] is False
    assert registry.specs([]) == []
    assert len(registry.list()) == 3
    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoTool())


@pytest.mark.parametrize(
    "expression,expected",
    [("17*23", "391"), ("-(2+3)*4", "-20"), ("7//2 + 7%2", "4"), ("1/4", "0.25")],
)
def test_calculator(expression: str, expected: str) -> None:
    result = builtin_registry().execute(
        ToolCall(id="1", name="calculator", arguments={"expression": expression})
    )
    assert result.ok
    assert result.output == expected


@pytest.mark.parametrize(
    "expression",
    ["__import__('os').system('whoami')", "2**999999", "True+1", "1/0", "1e101", "[1]", "2+"],
)
def test_unsafe_or_invalid_calculator_input(expression: str) -> None:
    result = builtin_registry().execute(
        ToolCall(id="1", name="calculator", arguments={"expression": expression})
    )
    assert not result.ok


def test_truncation_and_invalid_arguments() -> None:
    registry = ToolRegistry([EchoTool()], max_output_length=16)
    result = registry.execute(ToolCall(id="1", name="echo", arguments={"text": "x" * 50}))
    assert result.ok and len(result.output) == 16 and "truncated" in result.output
    assert not registry.execute(ToolCall(id="1", name="echo", arguments={"text": 2})).ok
    assert not registry.execute(ToolCall(id="1", name="get_time", arguments={})).ok
