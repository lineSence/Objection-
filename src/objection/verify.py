"""`verify`: independent short answers → weighted vote → unanimous? stop early : anti-conformist critique → re-vote.

The verdict is computed by code from the votes (never by a chair model), see D-014.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from . import prompts
from .engine import Engine, as_float, ctx_block, parse_json, phase_cost
from .schemas import Run, Verdict
from .voting import normalize, tally


async def run_verify(engine: Engine, run: Run, first: list[dict[str, Any]] | None = None,
                     models: list[str] | None = None) -> Verdict:
    """`first`: answers already collected (quick → escalation); only the remaining `models` are asked."""
    models = models or run.models
    answers = list(first or [])
    have = {a["model_id"] for a in answers}
    todo = [m for m in models if m not in have]
    if todo:
        answers += await independent(engine, run, todo)
    if not answers:
        raise RuntimeError("no model in the council produced an answer")
    t = vote(engine, run, answers, phase="vote")
    if t["unanimous"] or len(answers) < 2:
        skip(engine, run, "unanimous" if t["unanimous"] else "single answer")
        v = await checked(engine, run, answers, t)
        v.stopped_early = t["unanimous"]
        v.saved_usd_est = round(phase_cost(engine, run, "independent"), 6) if t["unanimous"] else 0.0
        return v
    if run.budget_exhausted:
        skip(engine, run, "budget exhausted")
        v = verdict(engine, run, answers, t)
        v.verdict = "uncertain"
        return v
    await critique(engine, run, answers)
    final = [dict(a, answer=a.get("final_answer", a["answer"])) for a in answers]
    t2 = vote(engine, run, final, phase="final")
    v = await checked(engine, run, final, t2)
    if run.budget_exhausted:
        v.verdict = "uncertain"
    return v


async def checked(engine: Engine, run: Run, answers: list[dict[str, Any]], t: dict[str, Any],
                  force: bool | None = None) -> Verdict:
    """Fact-check the vote groups (Verifier, M3) when enabled; evidence outranks votes."""
    from .verifier import check_votes, skip as skip_check

    on = engine.should_check(run, "verify") if force is None else force
    if not on or run.budget_exhausted:
        skip_check(engine, run, "budget exhausted" if on else "fact-checking is off")
        return verdict(engine, run, answers, t)
    t2, note, claims = await check_votes(engine, run, t, len(answers))
    v = verdict(engine, run, answers, t2)
    v.claims = claims
    v.fact_override = note
    return v


async def independent(engine: Engine, run: Run, models: list[str], phase: str = "independent") -> list[dict[str, Any]]:
    engine.emit(run, "phase_started", phase=phase, data={"models": models})
    started = time.perf_counter()
    user = prompts.VERIFIER_TEMPLATE.format(question=run.question, context=ctx_block(run.context))

    async def one(model_id: str) -> dict[str, Any] | None:
        c = await engine.call(run, engine.config.model(model_id), prompts.VERIFIER, user, phase=phase, json_mode=True)
        if c is None:
            return None
        d = parse_json(c.text)
        answer = str(d.get("answer") if d.get("answer") is not None else c.text.strip().splitlines()[0] if c.text.strip() else "")
        a = {"model_id": model_id, "answer": answer[:500], "reasoning": str(d.get("reasoning") or "")[:2000],
             "confidence": as_float(d.get("confidence"))}
        engine.emit(run, "answer", phase=phase, model_id=model_id, data={
            "text": a["reasoning"] or c.text, "position": a["answer"][:200], "answer": a["answer"],
            "key": normalize(a["answer"]), "confidence": a["confidence"], "usage": c.usage.model_dump()})
        return a

    results = await asyncio.gather(*(one(m) for m in models))
    answers = [r for r in results if r]
    engine.emit(run, "phase_finished", phase=phase, data={
        "answered": len(answers), "failed": len(results) - len(answers),
        "latency_s": round(time.perf_counter() - started, 2)})
    return answers


def weights(engine: Engine) -> dict[str, float]:
    return {m.id: m.weight for m in engine.config.models}


def vote(engine: Engine, run: Run, answers: list[dict[str, Any]], *, phase: str) -> dict[str, Any]:
    engine.emit(run, "phase_started", phase=phase, data={"answers": len(answers)})
    t = tally(answers, weights(engine))
    for g in t["votes"]:  # keep the best reasoning for each group
        best = max((a for a in answers if a["model_id"] in g["models"]), key=lambda a: a.get("confidence") or 0)
        g["reasoning"] = best.get("reasoning", "")
    engine.emit(run, "phase_finished", phase=phase, data={
        "votes": t["votes"], "unanimous": t["unanimous"], "tie": t["tie"], "share": t["share"],
        "agreement": t["agreement"]})
    return t


def skip(engine: Engine, run: Run, reason: str) -> None:
    for phase in ("critique", "final"):
        engine.emit(run, "phase_started", phase=phase, data={"skipped": True, "reason": reason})
        engine.emit(run, "phase_finished", phase=phase, data={"skipped": True, "reason": reason, "latency_s": 0})


async def critique(engine: Engine, run: Run, answers: list[dict[str, Any]]) -> None:
    """Everyone sees every other answer, anonymised and shuffled; changing position needs a named argument."""
    phase = "critique"
    engine.emit(run, "phase_started", phase=phase, data={"topology": "all-to-all, shuffled"})
    started = time.perf_counter()

    async def one(me: dict[str, Any]) -> None:
        peers = [a for a in answers if a is not me]
        random.shuffle(peers)
        others = "\n\n".join(f"[{j}] answer: {p['answer']}\nreasoning: {p['reasoning']}" for j, p in enumerate(peers, 1))
        user = prompts.VERIFY_CRITIC_TEMPLATE.format(question=run.question, context=ctx_block(run.context),
                                                     own=me["answer"], own_reasoning=me["reasoning"], others=others)
        c = await engine.call(run, engine.config.model(me["model_id"]), prompts.VERIFY_CRITIC, user, phase=phase,
                              json_mode=True)
        if c is None:
            return
        d = parse_json(c.text)
        new = str(d.get("answer") or me["answer"])[:500]
        changed = normalize(new) != normalize(me["answer"])
        me["final_answer"] = new
        me["changed"] = changed
        objections = []
        for o in d.get("objections") or []:
            if isinstance(o, dict):
                try:
                    objections.append({"target": peers[int(o.get("target")) - 1]["model_id"], "text": str(o.get("text", ""))})
                except (TypeError, ValueError, IndexError):
                    continue
        engine.emit(run, "critique", phase=phase, model_id=me["model_id"], data={
            "position": new[:200], "answer": new, "key": normalize(new), "changed": changed,
            "reason": d.get("reason") if changed else None, "objections": objections, "supports": [],
            "saw": [p["model_id"] for p in peers], "usage": c.usage.model_dump()})

    await asyncio.gather(*(one(a) for a in answers))
    engine.emit(run, "phase_finished", phase=phase, data={
        "changed": [a["model_id"] for a in answers if a.get("changed")],
        "latency_s": round(time.perf_counter() - started, 2)})


def verdict(engine: Engine, run: Run, answers: list[dict[str, Any]], t: dict[str, Any]) -> Verdict:
    w = t["winner"]
    minority = t["votes"][1:]
    uncertain = t["tie"] or w["key"] == "unknown" or t["share"] < 0.5
    return Verdict(
        answer=w["answer"] if not t["tie"] else " / ".join(g["answer"] for g in t["votes"] if g["weight"] == w["weight"]),
        verdict="uncertain" if uncertain else None,
        confidence=t["share"],
        agreement=t["agreement"],
        votes=[{k: g[k] for k in ("answer", "key", "models", "weight", "reasoning")} for g in t["votes"]],
        consensus=[w["reasoning"]] if t["unanimous"] and w.get("reasoning") else [],
        disputed=[] if len(t["votes"]) < 2 else [{"point": run.question[:200], "positions": {
            a["model_id"]: a["answer"] for a in answers}}],
        minority_report="; ".join(f"{g['answer']} ({', '.join(g['models'])}): {g.get('reasoning', '')}".strip()
                                  for g in minority) or None,
    )
