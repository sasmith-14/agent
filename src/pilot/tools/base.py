from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, ConfigDict

from pilot.providers.base import ToolSpec

Risk = Literal["low", "medium", "high"]


class ToolArgs(BaseModel):
    # Reject invented arguments so model mistakes become actionable feedback.
    model_config = ConfigDict(extra="forbid", strict=True)


class ToolResult(BaseModel):
    ok: bool
    output: str


class Tool[Params: BaseModel](ABC):
    name: str
    description: str
    params_model: type[Params]
    risk: Risk

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters_json_schema=self.params_model.model_json_schema(),
        )

    @abstractmethod
    def run(self, args: Params) -> ToolResult: ...
