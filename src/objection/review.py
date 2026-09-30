"""`review`: independent reviews → merge duplicates → anonymous cross-check of every finding → verdict by code."""

from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any

from . import prompts
from .engine import BudgetExceeded, Engine, ctx_block, parse_json
from .schemas import SEVERITY_ORDER, Finding, Objection, Run, Verdict

MAX_TARGET_CHARS = 120_000


def _sev(v: Any) -> str:
    v = str(v or "").lower()
    return v if v in SEVERITY_ORDER else "medium"


async def run_review(engine: Engine, run: Run) -> Verdict:
    target = (run.target or "")[:MAX_TARGET_CHARS]
    raw = await independent(engine, run, target)
    active = [m for m in run.models if m in raw]
    if not active:
        raise RuntimeError("no reviewer produced a review")
    all_findings = [f for m in active for f in raw[m]]
    if not all_findings:
        return _verdict(run, [], active, note="no findings from any reviewer")
    try:
        engine.check_budget(run)
        findings = await merge(engine, run, all_findings, active)
        engine.check_budget(run)
        await crosscheck(engine, run, target, findings, active)
    except BudgetExceeded as exc:
        # Partial result, never a silent pass (docs/integrations.md).
        findings = locals().get("findings") or _singletons(all_findings)
        for f in findings:
            f.confirmed_by = list(dict.fromkeys(f.reported_by))
        v = _verdict(run, findings, active, note=str(exc))
        v.verdict = "uncertain"
        return v
    v = _verdict(run, findings, active)
    if run.budget_exhausted and v.verdict == "pass":  # some cross-checks were skipped: never a silent pass
        v.verdict = "uncertain"
        v.answer += " Note: budget exhausted, some cross-checks were skipped."
    return v


async def independent(engine: Engine, run: Run, target: str) -> dict[str, list[dict[str, Any]]]:
    phase = "independent"
    engine.emit(run, "phase_started", phase=phase, data={"models": run.models})
    started = time.perf_counter()
    user = prompts.REVIEWER_TEMPLATE.format(kind=run.target_kind, question=run.question,
                                            context=ctx_block(run.context), target=target)
    out: dict[str, list[dict[str, Any]]] = {}

    async def one(model_id: str) -> None:
        c = await engine.call(run, engine.config.model(model_id), prompts.REVIEWER, user, phase=phase, json_mode=True)
        if c is None:
            return
        d = parse_json(c.text)
        items = []
        for k, f in enumerate(d.get("findings") or [], 1):
            if isinstance(f, dict) and f.get("title"):
                items.append({"id": f"{model_id}#{k}", "model_id": model_id, "title": str(f["title"]),
                              "severity": _sev(f.get("severity")), "location": f.get("location"),
                              "detail": str(f.get("detail") or ""), "suggestion": f.get("suggestion")})
        out[model_id] = items
        summary = str(d.get("summary") or "")
        engine.emit(run, "answer", phase=phase, model_id=model_id, data={
            "text": c.text, "position": f"{d.get('overall', '?')}: {summary}"[:200] if summary else f"{len(items)} findings",
            "overall": d.get("overall"), "findings": items, "usage": c.usage.model_dump()})

    await asyncio.gather(*(one(m) for m in run.models))
    engine.emit(run, "phase_finished", phase=phase, data={
        "answered": len(out), "failed": len(run.models) - len(out),
        "findings": sum(len(v) for v in out.values()), "latency_s": round(time.perf_counter() - started, 2)})
    return out


def _singletons(items: list[dict[str, Any]]) -> list[Finding]:
    return [Finding(id=f"F{i}", title=f["title"], severity=f["severity"], location=f.get("location"),
                    detail=f["detail"], suggestion=f.get("suggestion"), reported_by=[f["model_id"]])
            for i, f in enumerate(items, 1)]


async def merge(engine: Engine, run: Run, items: list[dict[str, Any]], active: list[str]) -> list[Finding]:
    phase = "analyze"
    started = time.perf_counter()
    if len(items) == 1:
        engine.emit(run, "phase_started", phase=phase, data={"skipped": True})
        findings = _singletons(items)
        engine.emit(run, "phase_finished", phase=phase, data={"groups": 1, "latency_s": 0})
        return findings
    engine.emit(run, "phase_started", phase=phase, data={"judge": engine.chair_candidates(run, active)[0].id})
    anon = [{"id": f"f{i}", "title": f["title"], "severity": f["severity"], "location": f.get("location"),
             "detail": f["detail"]} for i, f in enumerate(items, 1)]  # reviewers stay anonymous
    user = prompts.DEDUPE_TEMPLATE.format(findings=json.dumps(anon, ensure_ascii=False, indent=1))
    judge, c = await engine.call_chair(run, active, prompts.DEDUPE, user, phase=phase)
    by_id = {a["id"]: items[i] for i, a in enumerate(anon)}
    findings: list[Finding] = []
    used: set[str] = set()
    for g in parse_json(c.text).get("groups") or []:
        if not isinstance(g, dict):
            continue
        members = [str(m) for m in g.get("members") or [] if str(m) in by_id and str(m) not in used]
        if not members:
            continue
        used.update(members)
        src = [by_id[m] for m in members]
        worst = max((s["severity"] for s in src), key=SEVERITY_ORDER.__getitem__)
        findings.append(Finding(
            id=f"F{len(findings) + 1}", title=str(g.get("title") or src[0]["title"]),
            severity=_sev(g.get("severity") or worst), location=g.get("location") or src[0].get("location"),
            detail=str(g.get("detail") or src[0]["detail"]), suggestion=g.get("suggestion") or src[0].get("suggestion"),
            reported_by=list(dict.fromkeys(s["model_id"] for s in src))))
    for a in anon:  # the chair must not drop findings
        if a["id"] not in used:
            f = by_id[a["id"]]
            findings.append(Finding(id=f"F{len(findings) + 1}", title=f["title"], severity=f["severity"],
                                    location=f.get("location"), detail=f["detail"], suggestion=f.get("suggestion"),
                                    reported_by=[f["model_id"]]))
    engine.emit(run, "phase_finished", phase=phase, model_id=judge.id, data={
        "judge": judge.id, "groups": len(findings), "from": len(items), "usage": c.usage.model_dump(),
        "findings": [f.model_dump() for f in findings], "latency_s": round(time.perf_counter() - started, 2)})
    return findings


async def crosscheck(engine: Engine, run: Run, target: str, findings: list[Finding], active: list[str]) -> None:
    phase = "critique"
    engine.emit(run, "phase_started", phase=phase, data={"findings": len(findings)})
    started = time.perf_counter()
    groups = [{"id": f.id, "title": f.title, "severity": f.severity, "location": f.location, "detail": f.detail}
              for f in findings]
    by_id = {f.id: f for f in findings}

    async def one(model_id: str) -> None:
        order = groups[:]
        random.shuffle(order)  # per-reviewer order: no position bias shared by the whole council
        user = prompts.CROSSCHECK_TEMPLATE.format(kind=run.target_kind, target=target,
                                                  groups=json.dumps(order, ensure_ascii=False, indent=1))
        c = await engine.call(run, engine.config.model(model_id), prompts.CROSSCHECK, user, phase=phase, json_mode=True)
        if c is None:
            return
        votes = []
        for v in parse_json(c.text).get("votes") or []:
            if isinstance(v, dict) and str(v.get("id")) in by_id:
                vote = str(v.get("vote", "unsure")).lower()
                votes.append({"id": str(v["id"]), "vote": vote if vote in {"confirm", "refute"} else "unsure",
                              "reason": str(v.get("reason") or "")})
        engine.emit(run, "critique", phase=phase, model_id=model_id, data={"votes": votes, "usage": c.usage.model_dump()})
        for v in votes:
            f = by_id[v["id"]]
            if v["vote"] == "confirm" and model_id not in f.confirmed_by:
                f.confirmed_by.append(model_id)
            elif v["vote"] == "refute":
                f.refuted_by.append(Objection(model_id=model_id, reason=v["reason"]))

    await asyncio.gather(*(one(m) for m in active))
    engine.emit(run, "phase_finished", phase=phase, data={"latency_s": round(time.perf_counter() - started, 2)})


def _verdict(run: Run, findings: list[Finding], active: list[str], note: str | None = None) -> Verdict:
    threshold = SEVERITY_ORDER[run.fail_on]
    for f in findings:
        refuters = {o.model_id for o in f.refuted_by}
        # A reporter counts as confirming unless it refuted the merged finding itself.
        f.confirmed_by = list(dict.fromkeys([m for m in f.reported_by if m not in refuters] + f.confirmed_by))
        c, r = len(f.confirmed_by), len(refuters)
        need = 1 if len(active) == 1 else 2
        f.status = "confirmed" if c > r and c >= need else "rejected" if r > c else "disputed"
    findings.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], {"confirmed": 0, "disputed": 1, "rejected": 2}[f.status]))
    blocking = [f for f in findings if f.status == "confirmed" and SEVERITY_ORDER[f.severity] >= threshold]
    unclear = [f for f in findings if f.status == "disputed" and SEVERITY_ORDER[f.severity] >= threshold]
    verdict = "fail" if blocking else "uncertain" if unclear else "pass"
    counts = {s: sum(f.status == s for f in findings) for s in ("confirmed", "disputed", "rejected")}
    summary = (f"{verdict}: {len(blocking)} confirmed finding(s) at or above '{run.fail_on}', "
               f"{len(unclear)} disputed at or above it; total {counts['confirmed']} confirmed, "
               f"{counts['disputed']} disputed, {counts['rejected']} rejected by cross-check.")
    agree = sum(1 for m in active if (m in {x for f in blocking for x in f.confirmed_by}) == bool(blocking))
    return Verdict(
        answer=summary + (f" Note: {note}." if note else ""),
        verdict=verdict,
        findings=findings,
        agreement=f"{agree}/{len(active)}",
        consensus=[f.title for f in findings if f.status == "confirmed" and len(f.confirmed_by) == len(active)],
        disputed=[{"point": f.title, "positions": {o.model_id: o.reason for o in f.refuted_by}} for f in findings
                  if f.status == "disputed"],
        assumptions=[],
    )
