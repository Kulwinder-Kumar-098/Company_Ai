"""Typed state and validated model outputs for the Company AI graph."""

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.models.employee import Employee

MAX_TOOL_CALLS = 3
MAX_FINAL_ANSWER_CHARACTERS = 4000


class PlannedToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, object]


class RequestPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["rag", "structured", "mixed", "unsupported"]
    tool_calls: list[PlannedToolCall] = Field(
        default_factory=list,
        max_length=MAX_TOOL_CALLS,
    )

    @model_validator(mode="after")
    def validate_route(self) -> "RequestPlan":
        if self.mode in {"rag", "unsupported"} and self.tool_calls:
            raise ValueError("This route cannot include tool calls.")
        if self.mode in {"structured", "mixed"} and not self.tool_calls:
            raise ValueError("This route requires registered tool calls.")
        return self


class GeneratedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(
        min_length=1,
        max_length=MAX_FINAL_ANSWER_CHARACTERS,
    )


class AgentState(TypedDict):
    query: str
    actor: Employee
    db: Session
    plan: RequestPlan | None
    safe_error: str | None
    rag_result: dict[str, object] | None
    tool_results: list[dict[str, object]]
    evidence: str
    answer: str
    sources: list[dict[str, object]]


class AggregatedEvidence(BaseModel):
    """Bounded, validated evidence passed to final generation."""

    model_config = ConfigDict(extra="forbid")

    documents: list[dict[str, object]] = Field(default_factory=list)
    structured_results: list[dict[str, object]] = Field(default_factory=list)
