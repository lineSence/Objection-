"""Verifier (M3, D-015): Claim Ledger + tools (SearXNG web search, Python in the sandbox) + judges + revision.

Statuses are decided by code: python claims by the script's `holds`, search claims by the judges' votes (decisive
votes must agree and outnumber none of the opposite). Evidence reaches models only inside <untrusted> blocks.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from . import prompts
from .engine import BudgetExceeded, Engine, parse_json
from .sandbox import run_python
from .schemas import Claim, Run, Verdict
from .search import SearchError, searxng
from .untrusted import suspicious, wrap
from .voting import normalize

STATUSES = ("supported", "refuted", "unverified")


def skip(engine: Engine, run: Run, reason: str) -> None:
    engine.emit(run, "phase_started", phase="check", data={"skipped": True, "reason": reason})
    engine.emit(run, "phase_finished", phase="check", data={"skipped": True, "reason": reason, "latency_s": 0})


def _claims_from(data: dict[str, Any], limit: int, answer: str | None = None) -> list[tuple[Claim, dict[str, Any]]]:
    out: list[tuple[Claim, dict[str, Any]]] = []
    for c in (data.get("claims") or [])[:limit]:
        if not isinstance(c, dict) or not str(c.get("text") or "").strip():
            continue
        method = str(c.get("method") or "search").lower()
        quote = c.get("quote")
        quote = str(quote) if quote and answer and str(quote) in answer else None
        out.append((Claim(id=f"C{len(out) + 1}", text=str(c["text"])[:500], quote=quote,
                         kind=str(c.get("kind") or "fact"), method=method if method in ("search", "python", "none") else "search",
                         query=(str(c["query"])[:300] if c.get("query") else None),
                         code=(str(c["code"])[:6000] if c.get("code") else None)), c))
    return out


async def _chair(engine: Engine, run: Run, answered: list[str], system: str, user: str) -> dict[str, Any] | None:
    try:
        _, c = await engine.call_chair(run, answered, system, user, phase="check")
    except (BudgetExceeded, RuntimeError):
        return None
    return parse_json(c.text)


async def check_answer(engine: Engine, run: Run, verdict: Verdict, answers: list[dict[str, Any]]) -> None:
    """deliberate: extract claims from the final answer (+ who asserted them), check, revise if something was refuted."""
    cfg = engine.config.verifier
    started = time.perf_counter()
    engine.emit(run, "phase_started", phase="check", data={"search": cfg.web_search, "python": cfg.python})
    answered = [a["model_id"] for a in answers]
    numbered = {str(i): a["model_id"] for i, a in enumerate(answers, 1)}
    user = prompts.CLAIM_EXTRACT_TEMPLATE.format(
        question=run.question, answer=verdict.answer, max_claims=cfg.max_claims,
        answers="\n\n".join(f"[{i}]\n{a.get('text', '')[:3000]}" for i, a in enumerate(answers, 1)))
    data = await _chair(engine, run, answered, prompts.CLAIM_EXTRACT, user)
    pairs = _claims_from(data or {}, cfg.max_claims, verdict.answer)
    claims = [c for c, _ in pairs]
    for c, raw in pairs:
        c.authors = [numbered[str(n)] for n in raw.get("answers") or [] if str(n) in numbered]
        c.in_answer = c.quote is not None or not raw.get("answers")
    await check_all(engine, run, claims)
    verdict.claims = claims
    refuted = [c for c in claims if c.status == "refuted" and c.in_answer]
    if refuted and cfg.revise:
        await revise(engine, run, verdict, answered)
    _finish(engine, run, claims, started, revised=verdict.revised)


async def check_votes(engine: Engine, run: Run, t: dict[str, Any], n_answers: int) -> tuple[dict[str, Any], str | None, list[Claim]]:
    """verify/quick: one claim per vote group; checked facts outrank votes. Returns (tally, override note, claims)."""
    cfg = engine.config.verifier
    started = time.perf_counter()
    groups = t["votes"][:4]
    engine.emit(run, "phase_started", phase="check", data={"search": cfg.web_search, "python": cfg.python,
                                                          "candidates": len(groups)})
    answered = [m for g in groups for m in g["models"]]
    user = prompts.CLAIM_PLAN_TEMPLATE.format(
        question=run.question, candidates="\n".join(f"[{i}] {g['answer']}" for i, g in enumerate(groups, 1)))
    data = await _chair(engine, run, answered, prompts.CLAIM_EXTRACT, user) or {}
    by_group: dict[int, Claim] = {}
    for c, raw in _claims_from(data, len(groups)):
        try:
            gi = int(raw.get("candidate")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= gi < len(groups) and gi not in by_group:
            c.authors = list(groups[gi]["models"])
            by_group[gi] = c
    for gi, g in enumerate(groups):  # the planner must not drop a candidate
        if gi not in by_group:
            by_group[gi] = Claim(id="", text=f"{run.question.strip()} → {g['answer']}", method="search",
                                 query=f"{run.question.strip()} {g['answer']}"[:300], authors=list(g["models"]))
    claims = [by_group[i] for i in sorted(by_group)]
    for i, c in enumerate(claims, 1):
        c.id = f"C{i}"
    await check_all(engine, run, claims)
    new_t, note = apply_evidence(t, claims, n_answers)
    _finish(engine, run, claims, started, override=note)
    return new_t, note, claims


def apply_evidence(t: dict[str, Any], claims: list[Claim], n_answers: int) -> tuple[dict[str, Any], str | None]:
    """A supported candidate beats any vote; refuted candidates are dropped. Pure function (unit-tested)."""
    votes = t["votes"]
    status = {i: c.status for i, c in enumerate(claims)}
    supported = [votes[i] for i, s in status.items() if s == "supported" and i < len(votes)]
    refuted = {id(votes[i]) for i, s in status.items() if s == "refuted" and i < len(votes)}
    winner = t["winner"]
    note = None
    if len(supported) >= 1:
        best = max(supported, key=lambda g: g["weight"])
        if best is not winner:
            note = (f"evidence supports «{best['answer']}» over the vote winner «{winner['answer']}»")
            winner = best
    elif id(winner) in refuted:
        rest = [g for g in votes if id(g) not in refuted]
        if rest:
            note = f"«{winner['answer']}» was refuted by evidence; next candidate «{rest[0]['answer']}» wins"
            winner = rest[0]
        else:
            note = "every candidate was refuted by evidence"
    if winner is t["winner"] and not note:
        return t, None
    order = [winner] + [g for g in votes if g is not winner]
    total = sum(g["weight"] for g in votes) or 1.0
    uncertain = note == "every candidate was refuted by evidence"
    return {**t, "votes": order, "winner": winner, "tie": False, "unanimous": t["unanimous"] and winner is t["winner"],
            "share": 0.0 if uncertain else max(round(winner["weight"] / total, 3), 0.5),
            "agreement": f"{len(winner['models'])}/{n_answers}", "evidence_winner": not uncertain}, note


def _finish(engine: Engine, run: Run, claims: list[Claim], started: float, **extra: Any) -> None:
    counts = {s: sum(c.status == s for c in claims) for s in STATUSES}
    engine.emit(run, "phase_finished", phase="check", data={
        **counts, "claims": len(claims), **{k: v for k, v in extra.items() if v},
        "latency_s": round(time.perf_counter() - started, 2)})


async def check_all(engine: Engine, run: Run, claims: list[Claim]) -> None:
    for c in claims:
        engine.emit(run, "claim", phase="check", data=c.model_dump(exclude={"status", "judges", "sources", "output"}))
    sem = asyncio.Semaphore(3)

    async def one(c: Claim) -> None:
        async with sem:
            await check_one(engine, run, c)
        engine.emit(run, "claim_checked", phase="check", data={
            "id": c.id, "status": c.status, "evidence": c.evidence, "sources": c.sources, "judges": c.judges,
            "output": c.output, "flagged": c.flagged, "method": c.method})

    await asyncio.gather(*(one(c) for c in claims))


async def check_one(engine: Engine, run: Run, c: Claim) -> None:
    cfg = engine.config.verifier
    if c.method == "python" and cfg.python and c.code:
        await _python(engine, run, c)
        if c.status != "unverified" or not cfg.web_search:
            return
        c.method = "search"  # the script failed: fall back to search
    if c.method == "search" and cfg.web_search:
        await _search(engine, run, c)
        return
    c.evidence = c.evidence or ("no tool can check this claim" if c.method == "none" else f"{c.method} is disabled")


async def _python(engine: Engine, run: Run, c: Claim) -> None:
    r = await asyncio.to_thread(run_python, c.code or "", engine.config.sandbox)
    out = r["output"]
    c.output = out[-2000:]
    holds = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d.get("holds"), bool):
                holds = d["holds"]
                c.evidence = f"python: holds={holds}, value={json.dumps(d.get('value'), ensure_ascii=False)[:300]}"
                break
    engine.emit(run, "tool_call", phase="check", data={
        "tool": "python", "claim": c.id, "input": c.code, "output": c.output, "exit_code": r["exit_code"],
        "ok": r["exit_code"] == 0 and holds is not None, "network_isolated": r["network_isolated"],
        "duration_s": r["duration_s"]})
    if r["exit_code"] == 0 and holds is not None:
        c.status = "supported" if holds else "refuted"
    else:
        c.evidence = f"python check failed (exit {r['exit_code']})"


async def _search(engine: Engine, run: Run, c: Claim) -> None:
    cfg = engine.config.verifier
    query = c.query or c.text
    started = time.perf_counter()
    try:
        results = await searxng(query, cfg)
    except SearchError as exc:
        engine.emit(run, "tool_call", phase="check", data={"tool": "search", "claim": c.id, "input": query,
                                                           "ok": False, "error": str(exc)})
        c.evidence = str(exc)
        return
    c.sources = results
    c.flagged = any(suspicious(r["title"] + " " + r["snippet"]) for r in results)
    engine.emit(run, "tool_call", phase="check", data={
        "tool": "search", "claim": c.id, "input": query, "ok": True, "results": results, "flagged": c.flagged,
        "duration_s": round(time.perf_counter() - started, 2)})
    if not results:
        c.evidence = "no search results"
        return
    evidence = "\n".join(f"[{i}] {wrap(r['title'] + ' — ' + r['snippet'], r['url'], 900)}" for i, r in enumerate(results, 1))
    user = prompts.CLAIM_JUDGE_TEMPLATE.format(claim=c.text, evidence=evidence)
    # Judges: prefer council members that did not assert the claim (independence), cheapest-first order otherwise.
    pool = [m for m in run.models if m not in c.authors] + [m for m in run.models if m in c.authors]
    judges = pool[: cfg.judges]

    async def judge(model_id: str) -> tuple[str, dict[str, Any]] | None:
        r = await engine.call(run, engine.config.model(model_id), prompts.CLAIM_JUDGE, user, phase="check", json_mode=True)
        return None if r is None else (model_id, parse_json(r.text))

    votes = [v for v in await asyncio.gather(*(judge(m) for m in judges)) if v]
    for model_id, d in votes:
        s = str(d.get("status", "unverified")).lower()
        c.judges[model_id] = s if s in STATUSES else "unverified"
    c.status = decide(list(c.judges.values()))
    best = next((d for m, d in votes if c.judges[m] == c.status), None)
    c.evidence = str((best or {}).get("evidence") or c.evidence or "")[:600] or None
    used = {int(x) for d in [best or {}] for x in d.get("sources") or [] if str(x).isdigit()}
    if used:
        c.sources = [r for i, r in enumerate(results, 1) if i in used] + [r for i, r in enumerate(results, 1) if i not in used]


def decide(votes: list[str]) -> str:
    """Decisive votes must agree; at least half of the judges must be decisive."""
    decisive = [v for v in votes if v in ("supported", "refuted")]
    if not decisive or len(set(decisive)) > 1 or len(decisive) * 2 < len(votes):
        return "unverified"
    return decisive[0]


async def revise(engine: Engine, run: Run, verdict: Verdict, answered: list[str]) -> None:
    listing = "\n".join(f"- [{c.status}] {c.text}" + (f" — evidence: {c.evidence}" if c.evidence else "")
                        for c in verdict.claims)
    user = prompts.REVISE_TEMPLATE.format(question=run.question, answer=verdict.answer, claims=listing)
    d = await _chair(engine, run, answered, prompts.REVISE, user)
    new = str((d or {}).get("answer") or "").strip()
    if not new or normalize(new) == normalize(verdict.answer):
        return
    verdict.original_answer = verdict.answer
    verdict.answer = new
    verdict.revised = True
    changes = [str(x) for x in (d or {}).get("changes") or []]
    verdict.assumptions = verdict.assumptions + [f"исправлено фактчеком: {x}" for x in changes]
    for c in verdict.claims:  # quotes point into the original text; keep only those still present
        if c.quote and c.quote not in new:
            c.quote = None
