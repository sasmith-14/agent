"""Policy stays separate from execution so approval cannot accidentally run a tool."""

from typing import Protocol

from pydantic import BaseModel
from rich.console import Console
from rich.prompt import Confirm

from pilot.config import Policy, PolicySettings
from pilot.events import Redactor
from pilot.providers.base import ToolCall
from pilot.tools.base import Risk


class ApprovalRequest(BaseModel):
    call: ToolCall
    risk: Risk
    run_id: str
    agent_id: str
    parent_id: str | None = None


class Approver(Protocol):
    def approve(self, request: ApprovalRequest) -> bool: ...


class AutoApprover:
    def approve(self, request: ApprovalRequest) -> bool:
        return True


class AutoDenier:
    def approve(self, request: ApprovalRequest) -> bool:
        return False


class CliApprover:
    def __init__(self, console: Console, redactor: Redactor | None = None) -> None:
        self.console = console
        self.redactor = redactor or Redactor()

    def approve(self, request: ApprovalRequest) -> bool:
        self.console.print(
            self.redactor.text(
                f"Tool: {request.call.name} | risk: {request.risk}\n"
                f"Arguments: {request.call.arguments}"
            ),
            markup=False,
        )
        try:
            return Confirm.ask("Allow this call?", default=False, console=self.console)
        except EOFError:
            return False


class PolicyDecision(BaseModel):
    policy: Policy
    allowed: bool


class PolicyEngine:
    def __init__(self, settings: PolicySettings, approver: Approver) -> None:
        self.settings = settings.model_copy(deep=True)
        self.approver = approver

    def effective_policy(self, tool_name: str) -> Policy:
        return self.settings.tools.get(tool_name, self.settings.default)

    def decide(self, request: ApprovalRequest) -> PolicyDecision:
        policy = self.effective_policy(request.call.name)
        allowed = policy == "allow" or (policy == "ask" and self.approver.approve(request))
        return PolicyDecision(policy=policy, allowed=allowed)
