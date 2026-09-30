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
    "critique",
    "phase_finished",
    "verdict",
    "run_finished",
    "run_failed",
]


def now() -> datetime:
    return datetime.now(timezone.utc)


Mode = Literal["deliberate", "review"]
Severity = Literal["critical", "high", "medium", "low", "info"]
SEVERITY_ORDER: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
TargetKind = Literal["diff", "plan", "file", "text"]


class RunRequest(BaseModel):
    question: str = Field(min_length=1)  # for review: what to focus on (may be a generic instruction)
    context: str | None = None
    mode: Mode = "deliberate"
    target: str | None = None  # review: the diff / plan / file content
    target_kind: TargetKind = "diff"
    fail_on: Severity = "high"  # review: minimal confirmed severity that fails the verdict
    models: list[str] | None = None  # pin models by id; default: from config
    budget_usd: float | None = None
    source: str = "web"  # web | cli | opencode | cline | mcp
    no_cache: bool = False


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


class Objection(BaseModel):
    model_id: str
    reason: str = ""


class Finding(BaseModel):
    id: str
    title: str
    severity: Severity = "medium"
    location: str | None = None
    detail: str = ""
    suggestion: str | None = None
    reported_by: list[str] = Field(default_factory=list)
    confirmed_by: list[str] = Field(default_factory=list)
    refuted_by: list[Objection] = Field(default_factory=list)
    status: Literal["confirmed", "disputed", "rejected"] = "disputed"


class Verdict(BaseModel):
    answer: str
    verdict: Literal["pass", "fail", "uncertain"] | None = None  # review only
    findings: list[Finding] = Field(default_factory=list)
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
    mode: Mode
    source: str
    target: str | None = None
    target_kind: TargetKind = "diff"
    fail_on: Severity = "high"
    cached: bool = False
    status: RunStatus = "queued"
    models: list[str] = Field(default_factory=list)
    budget_usd: float
    cost_usd: float = 0.0
    latency_s: float | None = None
    verdict: Verdict | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=now)
    finished_at: datetime | None = None
