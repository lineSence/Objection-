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
    "route",
    "test_result",
    "claim",
    "tool_call",
    "claim_checked",
    "phase_finished",
    "verdict",
    "run_finished",
    "run_failed",
]


def now() -> datetime:
    return datetime.now(timezone.utc)


Mode = Literal["auto", "deliberate", "review", "verify", "quick", "code"]
RESOLVED_MODES = ("deliberate", "review", "verify", "quick", "code")
Severity = Literal["critical", "high", "medium", "low", "info"]
SEVERITY_ORDER: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
TargetKind = Literal["diff", "plan", "file", "text"]


class RunRequest(BaseModel):
    question: str = Field(min_length=1)  # for review: what to focus on (may be a generic instruction)
    context: str | None = None
    mode: Mode | None = None  # None → config defaults.mode (default "auto": the router picks)
    target: str | None = None  # review: the diff / plan / file content
    target_kind: TargetKind = "diff"
    fail_on: Severity = "high"  # review: minimal confirmed severity that fails the verdict
    models: list[str] | None = None  # pin models by id; default: from config
    budget_usd: float | None = None
    source: str = "web"  # web | cli | opencode | cline | mcp
    no_cache: bool = False
    # code mode
    tests_cmd: str | None = None  # shell command that must pass, e.g. "pytest -q"
    workdir: str | None = None  # project directory copied into a temp dir for every candidate
    solution_path: str | None = None  # file the solution is written to, relative to workdir
    check_facts: bool | None = None  # None → config verifier.enabled / verifier.modes


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


ClaimStatus = Literal["supported", "refuted", "unverified"]


class Claim(BaseModel):
    """A checkable statement from the council's answers (Claim Ledger, M3)."""
    id: str
    text: str
    quote: str | None = None  # exact span of the final answer to highlight
    kind: str = "fact"  # fact | number | code | citation
    method: str = "search"  # search | python | none
    query: str | None = None
    code: str | None = None
    authors: list[str] = Field(default_factory=list)  # council members that asserted it
    in_answer: bool = True
    status: ClaimStatus = "unverified"
    evidence: str | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)  # [{title, url, snippet}]
    output: str | None = None  # python output
    judges: dict[str, str] = Field(default_factory=dict)  # model_id → status
    flagged: bool = False  # evidence looked like a prompt injection


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
    # M2
    votes: list[dict[str, Any]] = Field(default_factory=list)  # [{answer, models, weight}] — verify / quick
    stopped_early: bool = False  # unanimous first round: critique skipped
    escalated_to: str | None = None  # quick → verify/deliberate when the two models disagreed
    solution: dict[str, Any] | None = None  # code: {model_id, filename, code, passed, output}
    candidates: list[dict[str, Any]] = Field(default_factory=list)  # code: every candidate with its test result
    saved_usd_est: float = 0.0  # estimated cost avoided by early stop / quick path
    # M3
    claims: list[Claim] = Field(default_factory=list)
    revised: bool = False  # the answer was rewritten because a claim in it was refuted
    original_answer: str | None = None
    fact_override: str | None = None  # verify: the vote winner was overturned by evidence


class Run(BaseModel):
    id: str
    question: str
    context: str | None = None
    mode: Mode  # resolved mode after routing ("auto" only until the router ran)
    requested_mode: Mode = "deliberate"
    route_reason: str | None = None
    source: str
    target: str | None = None
    target_kind: TargetKind = "diff"
    fail_on: Severity = "high"
    cached: bool = False
    status: RunStatus = "queued"
    models: list[str] = Field(default_factory=list)
    budget_usd: float
    cost_usd: float = 0.0
    budget_exhausted: bool = False  # some calls were skipped to stay within budget_usd
    tests_cmd: str | None = None
    workdir: str | None = None
    solution_path: str | None = None
    check_facts: bool | None = None  # None → by config (verifier.enabled and mode in verifier.modes)
    latency_s: float | None = None
    verdict: Verdict | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=now)
    finished_at: datetime | None = None
