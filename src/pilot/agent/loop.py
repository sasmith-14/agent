"""The loop knows no provider-specific request or response formats."""

from uuid import uuid4

from pydantic import BaseModel

from pilot.config import Limits
from pilot.events import (
    EventBus,
    LLMRequested,
    LLMResponded,
    PolicyDecided,
    RunFinished,
    RunStarted,
    RunStatus,
    ToolFinished,
    ToolRequested,
)
from pilot.permissions import ApprovalRequest, PolicyEngine
from pilot.providers.base import LLMProvider, Message, ProviderError, Usage
from pilot.tools.base import ToolResult
from pilot.tools.registry import ToolRegistry


class RunResult(BaseModel):
    status: RunStatus
    final_text: str
    steps: int
    usage: Usage
    run_id: str


class Agent:
    def __init__(
        self,
        agent_id: str,
        provider: LLMProvider,
        tools: ToolRegistry,
        policy: PolicyEngine,
        bus: EventBus,
        system_prompt: str,
        limits: Limits,
        parent_id: str | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.parent_id = parent_id
        self.provider = provider
        self.tools = tools
        self.policy = policy
        self.bus = bus
        self.system_prompt = system_prompt
        self.limits = limits.model_copy(deep=True)

    def run(self, task: str) -> RunResult:
        run_id = uuid4().hex
        context = {"run_id": run_id, "agent_id": self.agent_id, "parent_id": self.parent_id}
        messages = [
            Message(role="system", content=self.system_prompt),
            Message(role="user", content=task),
        ]
        usage = Usage()
        steps = 0
        status: RunStatus = "limit_reached"
        final_text = "Step limit reached"
        self.bus.emit(RunStarted(**context, task=task))
        try:
            for step in range(1, self.limits.max_steps + 1):
                specs = self.tools.specs()
                self.bus.emit(LLMRequested(**context, step=step, messages=messages, tools=specs))
                steps = step
                response = self.provider.generate(messages, specs)
                if response.message.role != "assistant":
                    raise ProviderError("Provider returned a non-assistant message")
                usage = Usage(
                    input_tokens=usage.input_tokens + response.usage.input_tokens,
                    output_tokens=usage.output_tokens + response.usage.output_tokens,
                )
                self.bus.emit(
                    LLMResponded(
                        **context, step=step, message=response.message, usage=response.usage
                    )
                )
                messages.append(response.message)
                # Usage is known only after a response; never execute tools once exhausted.
                if usage.total_tokens >= self.limits.max_total_tokens:
                    final_text = "Token limit reached"
                    break
                if not response.message.tool_calls:
                    status, final_text = "completed", response.message.content
                    break
                for call in response.message.tool_calls:
                    tool = self.tools.get(call.name)
                    risk = tool.risk if tool else "high"
                    self.bus.emit(ToolRequested(**context, call=call, risk=risk))
                    decision = self.policy.decide(ApprovalRequest(**context, call=call, risk=risk))
                    self.bus.emit(
                        PolicyDecided(
                            **context,
                            tool_call_id=call.id,
                            tool_name=call.name,
                            **decision.model_dump(),
                        )
                    )
                    result = (
                        self.tools.execute(call)
                        if decision.allowed
                        else self.tools.truncate(ToolResult(ok=False, output="Denied by policy"))
                    )
                    self.bus.emit(
                        ToolFinished(
                            **context, tool_call_id=call.id, tool_name=call.name, result=result
                        )
                    )
                    messages.append(
                        Message(role="tool", content=result.model_dump_json(), tool_call_id=call.id)
                    )
        except ProviderError as exc:
            status, final_text = "error", str(exc)
        except KeyboardInterrupt:
            status, final_text = "error", "Run interrupted"
        result = RunResult(
            status=status, final_text=final_text, steps=steps, usage=usage, run_id=run_id
        )
        self.bus.emit(RunFinished(**context, **result.model_dump(exclude={"run_id"})))
        return result
