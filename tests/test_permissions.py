import pytest

from pilot.config import Policy, PolicySettings
from pilot.permissions import (
    ApprovalRequest,
    AutoApprover,
    AutoDenier,
    PolicyEngine,
)
from pilot.providers.base import ToolCall


@pytest.mark.parametrize(
    "policy,approved,expected",
    [
        ("allow", False, True),
        ("deny", True, False),
        ("ask", True, True),
        ("ask", False, False),
    ],
)
def test_policy(policy: Policy, approved: bool, expected: bool) -> None:
    engine = PolicyEngine(
        PolicySettings(default=policy), AutoApprover() if approved else AutoDenier()
    )
    decision = engine.decide(
        ApprovalRequest(
            call=ToolCall(id="1", name="echo", arguments={}),
            risk="low",
            run_id="run",
            agent_id="agent",
        )
    )
    assert decision.policy == policy
    assert decision.allowed is expected


def test_per_tool_override_and_independent_settings() -> None:
    settings = PolicySettings(tools={"echo": "deny"})
    engine = PolicyEngine(settings, AutoApprover())
    settings.tools["echo"] = "allow"
    assert engine.effective_policy("echo") == "deny"
    assert engine.effective_policy("calculator") == "ask"
