"""MCP server (stdio) for agentic coding tools — OpenCode, Cline and any MCP client (D-005)."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer

from .config import Config, load_config
from .engine import Engine
from .providers import health_check
from .schemas import Run, RunRequest
from .store import RunStore
from .voting import normalize

INSTRUCTIONS = """Objection! is a council of independent LLMs.
- council_review: before committing or opening a PR, pass the diff (or a plan before implementing it). Branch on
  `verdict`: "fail" -> fix the confirmed findings; "uncertain" -> look at the disputed findings; "pass" -> proceed.
- council_ask: architecture decisions, choosing between approaches, a second opinion when stuck. mode="auto" (default)
  lets the router pick the cheapest protocol: quick (2 models, escalate on disagreement), verify, deliberate.
- council_verify: fact-check one claim (an API behaviour, a version, a number) -> confirmed | refuted | unverified.
  Answers carry `claims` checked by web search / Python with sources; a checked fact outweighs the vote.
- council_solve: several models write a solution, your tests decide (pass tests_cmd + workdir). Model code is executed
  in the local sandbox (temp copy of workdir, no secrets, no network where possible). Apply `solution.code` yourself.
- council_models: which models are in the pool and whether they respond.
Reviewers see only what you pass (clean context): include the diff and any file content they need.
Results are cached by input, so repeating the same call is free."""

UI_URL = os.environ.get("OBJECTION_UI_URL", "http://127.0.0.1:6967")


def _client_name(ctx: Context | None) -> str:
    try:
        name = ctx.session.client_params.client_info.name  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return "mcp"
    name = (name or "mcp").lower()
    for known in ("opencode", "cline"):
        if known in name:
            return known
    return name[:32]


def _result(run: Run) -> dict[str, Any]:
    v = run.verdict
    out: dict[str, Any] = {
        "run_id": run.id, "status": run.status, "mode": run.mode, "cached": run.cached,
        "cost_usd": round(run.cost_usd, 6), "latency_s": run.latency_s, "models": run.models,
        "ui": f"{UI_URL}/#/runs/{run.id}",
    }
    if run.error:
        out["error"] = run.error
    if run.requested_mode != run.mode:
        out["routed"] = {"from": run.requested_mode, "reason": run.route_reason}
    if run.budget_exhausted:
        out["budget_exhausted"] = True
    if v:
        if v.votes:
            out["votes"] = [{"answer": g["answer"], "models": len(g["models"]), "weight": g["weight"]} for g in v.votes]
        if v.stopped_early:
            out["stopped_early"] = True
            out["saved_usd_est"] = v.saved_usd_est
        if v.escalated_to:
            out["escalated_to"] = v.escalated_to
        if v.claims:
            out["claims"] = [{"id": c.id, "text": c.text, "status": c.status, "evidence": c.evidence,
                              "sources": [x["url"] for x in c.sources[:3]], "flagged": c.flagged or None}
                             for c in v.claims]
        if v.revised:
            out["revised"] = True
        if v.fact_override:
            out["fact_override"] = v.fact_override
        if run.mode == "code":
            out["verdict"] = v.verdict
            out["solution"] = v.solution
            out["candidates"] = [{k: c.get(k) for k in ("model_id", "filename", "round", "passed")} for c in v.candidates]
        elif v.verdict and run.mode != "review":
            out["verdict"] = v.verdict
        out.update(answer=v.answer, confidence=v.confidence, agreement=v.agreement, consensus=v.consensus,
                   disputed=v.disputed, minority_report=v.minority_report, assumptions=v.assumptions)
        if run.mode == "review":
            out["verdict"] = v.verdict
            out["findings"] = [
                {"id": f.id, "status": f.status, "severity": f.severity, "title": f.title, "location": f.location,
                 "detail": f.detail, "suggestion": f.suggestion, "confirmed_by": len(f.confirmed_by),
                 "refuted_by": len(f.refuted_by), "objections": [o.reason for o in f.refuted_by]}
                for f in v.findings if f.status != "rejected"
            ]
            out["rejected_findings"] = sum(f.status == "rejected" for f in v.findings)
    return out


def build_server(config: Config | None = None, store: RunStore | None = None) -> MCPServer:
    config = config or load_config()
    engine = Engine(config, store or RunStore(config.storage.resolved))
    server = MCPServer(name="objection", instructions=INSTRUCTIONS)

    async def run_with_progress(req: RunRequest, ctx: Context | None) -> dict[str, Any]:
        req.source = _client_name(ctx)
        run = engine.create_run(req)
        if not req.no_cache and (hit := engine.cached_for(run)):
            return _result(hit)
        q = engine.bus.subscribe(run.id)
        task = asyncio.create_task(engine.execute(run))
        step = 0
        try:
            while not task.done():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=1)
                except asyncio.TimeoutError:
                    continue
                if ev.type == "phase_started" and ctx is not None:
                    step += 1
                    try:
                        await ctx.report_progress(step, None, f"{ev.phase}")
                    except Exception:  # noqa: BLE001 — progress is best-effort
                        pass
            return _result(await task)
        finally:
            engine.bus.unsubscribe(run.id, q)

    @server.tool(description=(
        "Ask the council a question (preset `deliberate`): independent answers, one anonymous critique round, "
        "synthesis that keeps disagreements. Use for decisions and second opinions."))
    async def council_ask(question: str, context: str | None = None,
                          mode: Literal["auto", "deliberate", "verify", "quick"] = "auto",
                          models: list[str] | None = None, check_facts: bool | None = None,
                          budget_usd: float | None = None, no_cache: bool = False,
                          ctx: Context | None = None) -> dict[str, Any]:
        return await run_with_progress(RunRequest(question=question, context=context, mode=mode, models=models,
                                                  check_facts=check_facts, budget_usd=budget_usd, no_cache=no_cache), ctx)

    @server.tool(description=(
        "Fact-check one claim with the council (preset `verify`): independent short answers, weighted vote, critique "
        "only on disagreement, then the Verifier checks the candidates with web search (SearXNG) or Python; checked "
        "evidence outranks votes. Returns status confirmed | refuted | unverified with votes, claims and sources."))
    async def council_verify(claim: str, context: str | None = None, models: list[str] | None = None,
                             check_facts: bool | None = None, budget_usd: float | None = None, no_cache: bool = False,
                             ctx: Context | None = None) -> dict[str, Any]:
        q = f"Claim: {claim}\nIs this claim true? Answer exactly true, false or unknown."
        out = await run_with_progress(RunRequest(question=q, context=context, mode="verify", models=models,
                                                 check_facts=check_facts, budget_usd=budget_usd, no_cache=no_cache), ctx)
        top = (out.get("votes") or [{}])[0]
        key = {"true": "confirmed", "false": "refuted"}.get(normalize(top.get("answer")))
        out["status"] = key if key and out.get("verdict") != "uncertain" else "unverified"
        out["claim"] = claim
        return out

    @server.tool(description=(
        "Solve a coding task with the council (preset `code`): every model writes the file, your tests_cmd runs on "
        "each candidate in a temporary copy of workdir, failing candidates get one fix round with the test log. "
        "Returns verdict pass|fail and `solution` {filename, code}. Model code runs in the local sandbox (not a VM)."))
    async def council_solve(task: str, tests_cmd: str | None = None, workdir: str | None = None,
                            solution_path: str | None = None, context: str | None = None,
                            models: list[str] | None = None, budget_usd: float | None = None,
                            no_cache: bool = False, ctx: Context | None = None) -> dict[str, Any]:
        return await run_with_progress(RunRequest(question=task, context=context, mode="code", tests_cmd=tests_cmd,
                                                  workdir=workdir, solution_path=solution_path, models=models,
                                                  budget_usd=budget_usd, no_cache=no_cache), ctx)

    @server.tool(description=(
        "Council review of a diff, plan or file (preset `review`): independent reviews, merged findings, anonymous "
        "cross-check of every finding. Returns verdict pass|fail|uncertain and findings with severity and status."))
    async def council_review(target: str, kind: Literal["diff", "plan", "file", "text"] = "diff",
                             instructions: str = "Find real problems: bugs, security, broken logic, missing edge cases.",
                             context: str | None = None,
                             fail_on: Literal["critical", "high", "medium", "low", "info"] = "high",
                             models: list[str] | None = None, budget_usd: float | None = None,
                             no_cache: bool = False, ctx: Context | None = None) -> dict[str, Any]:
        return await run_with_progress(RunRequest(question=instructions, context=context, mode="review", target=target,
                                                  target_kind=kind, fail_on=fail_on, models=models,
                                                  budget_usd=budget_usd, no_cache=no_cache), ctx)

    @server.tool(description="List the model pool; with check=true also health-check every enabled model.")
    async def council_models(check: bool = False) -> dict[str, Any]:
        models = [{"id": m.id, "model": m.model, "local": m.is_local, "enabled": m.enabled} for m in config.models]
        if check:
            enabled = config.enabled_models
            results = await asyncio.gather(*(health_check(m) for m in enabled))
            status = {m.id: {"ok": ok, "detail": d, "latency_s": round(t, 2)} for m, (ok, d, t) in zip(enabled, results)}
            for m in models:
                m.update(status.get(m["id"], {}))
        return {"models": models, "council_size": config.defaults.council.size}

    return server


def run_stdio() -> None:
    # stdout belongs to the MCP protocol: keep every library quiet and log to stderr.
    logging.basicConfig(level=logging.WARNING, stream=__import__("sys").stderr)
    os.environ.setdefault("LITELLM_LOG", "ERROR")
    try:
        import litellm

        litellm.suppress_debug_info = True
    except Exception:  # noqa: BLE001
        pass
    build_server().run("stdio")
