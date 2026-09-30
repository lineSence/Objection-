"""Data exchanged between the engine, the store, the CLI and the Web UI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

RunStatus = Literal["queued", "running", "done", "failed"]
EventType = Literal[
    "run_started",
    "phase_started",
    "answer",
    "model_error",
    "phase_finished",
    "verdict",
    "run_finished",
    "run_failed",
]


def now() -> datetime:
    return datetime.now(timezone.utc)


class RunRequest(BaseModel):
    question: str = Field(min_length=1)
    context: str | None = None
    mode: str = "deliberate"
    models: list[str] | None = None  # pin models by id; default: from config
    budget_usd: float | None = None
    source: str = "web"  # web | cli | opencode | cline | mcp


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0


class Event(BaseModel):
    run_id: str
    seq: int
    type: EventType
    phase: str | None = None
    model_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=now)


class Verdict(BaseModel):
    answer: str
    confidence: float | None = None
    agreement: str | None = None  # e.g. "3/4"
    consensus: list[str] = Field(default_factory=list)
    disputed: list[dict[str, Any]] = Field(default_factory=list)
    minority_report: str | None = None
    assumptions: list[str] = Field(default_factory=list)


class Run(BaseModel):
    id: str
    question: str
    context: str | None = None
    mode: str
    source: str
    status: RunStatus = "queued"
    models: list[str] = Field(default_factory=list)
    budget_usd: float
    cost_usd: float = 0.0
    latency_s: float | None = None
    verdict: Verdict | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=now)
    finished_at: datetime | None = None
