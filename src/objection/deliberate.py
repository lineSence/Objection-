"""`deliberate`: independent answers → one anonymous critique round (sparse) → chair synthesis."""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from . import prompts
from .engine import BudgetExceeded, Engine, as_float, ctx_block, parse_json
from .schemas import Run, Verdict


async def run_deliberate(engine: Engine, run: Run) -> Verdict:
    answers = await independent(engine, run)
    if not answers:
        raise RuntimeError("no model in the council produced an answer")
    try:
        engine.check_budget(run)
        if engine.config.defaults.max_critique_rounds >= 1 and len(answers) >= 2:
            await critique(engine, run, answers)
        verdict = await synthesize(engine, run, answers)
    except BudgetExceeded as exc:
        # Partial result: the independent answers without synthesis, clearly marked.
        return Verdict(
            answer=f"Budget exhausted ({exc}); synthesis skipped. First independent answer:\n\n{answers[0]['text']}",
            verdict="uncertain",
            agreement=None,
            disputed=[{"point": run.question[:200], "positions": {a["model_id"]: a["position"] for a in answers}}]
            if len(answers) > 1 else [],
        )
    if engine.should_check(run):
        from .verifier import check_answer

        await check_answer(engine, run, verdict, answers)
    else:
        from .verifier import skip

        skip(engine, run, "fact-checking is off")
    return verdict


async def independent(engine: Engine, run: Run) -> list[dict[str, Any]]:
    phase = "independent"
    engine.emit(run, "phase_started", phase=phase, data={"models": run.models})
    user = run.question + ctx_block(run.context)
    started = time.perf_counter()

    async def one(model_id: str) -> dict[str, Any] | None:
        c = await engine.call(run, engine.config.model(model_id), prompts.INDEPENDENT, user, phase=phase)
        if c is None:
            return None
        position = c.text.strip().splitlines()[0][:200] if c.text.strip() else ""
        engine.emit(run, "answer", phase=phase, model_id=model_id,
                    data={"text": c.text, "position": position, "usage": c.usage.model_dump()})
        return {"model_id": model_id, "text": c.text, "position": position}

    results = await asyncio.gather(*(one(m) for m in run.models))
    answers = [r for r in results if r]
    engine.emit(run, "phase_finished", phase=phase, data={
        "answered": len(answers), "failed": len(results) - len(answers),
        "latency_s": round(time.perf_counter() - started, 2)})
    return answers


async def critique(engine: Engine, run: Run, answers: list[dict[str, Any]]) -> None:
    """Sparse topology: each member sees at most 2 other answers (ring), anonymised and numbered."""
    phase = "critique"
    engine.emit(run, "phase_started", phase=phase, data={"topology": "ring-2"})
    started = time.perf_counter()
    n = len(answers)

    async def one(i: int) -> None:
        me = answers[i]
        peers = [answers[(i + k) % n] for k in range(1, min(3, n))]
        others = "\n\n".join(f"[{j}]\n{p['text']}" for j, p in enumerate(peers, 1))
        user = prompts.CRITIC_TEMPLATE.format(question=run.question, context=ctx_block(run.context),
                                              own=me["text"], others=others)
        c = await engine.call(run, engine.config.model(me["model_id"]), prompts.CRITIC, user, phase=phase, json_mode=True)
        if c is None:
            return
        d = parse_json(c.text)

        def mapped(key: str) -> list[dict[str, str]]:
            out = []
            for o in d.get(key) or []:
                if isinstance(o, dict):
                    try:
                        target = peers[int(o.get("target")) - 1]["model_id"]
                    except (TypeError, ValueError, IndexError):
                        continue
                    out.append({"target": target, "text": str(o.get("text", ""))})
            return out

        position = str(d.get("position") or me["position"])
        changed = bool(d.get("changed"))
        me["final_position"] = position
        me["changed"] = changed
        me["reason"] = d.get("reason")
        engine.emit(run, "critique", phase=phase, model_id=me["model_id"], data={
            "position": position, "changed": changed, "reason": d.get("reason"),
            "objections": mapped("objections"), "supports": mapped("supports"),
            "saw": [p["model_id"] for p in peers], "usage": c.usage.model_dump()})

    await asyncio.gather(*(one(i) for i in range(n)))
    engine.emit(run, "phase_finished", phase=phase, data={
        "changed": [a["model_id"] for a in answers if a.get("changed")],
        "latency_s": round(time.perf_counter() - started, 2)})


async def synthesize(engine: Engine, run: Run, answers: list[dict[str, Any]]) -> Verdict:
    phase = "synthesize"
    started = time.perf_counter()
    answered = [a["model_id"] for a in answers]
    engine.emit(run, "phase_started", phase=phase, data={"judge": engine.chair_candidates(run, answered)[0].id})
    order = answers[:]
    if engine.config.defaults.anonymize:
        random.shuffle(order)

    def block(i: int, a: dict[str, Any]) -> str:
        s = f"[{i}]\n{a['text']}"
        if "final_position" in a:
            s += f"\nAfter critique: {a['final_position']}"
            if a.get("changed") and a.get("reason"):
                s += f" (changed because: {a['reason']})"
        return s

    user = prompts.CHAIR_TEMPLATE.format(question=run.question, context=ctx_block(run.context),
                                         answers="\n\n".join(block(i, a) for i, a in enumerate(order, 1)))
    judge, c = await engine.call_chair(run, answered, prompts.CHAIR, user, phase=phase)
    data = parse_json(c.text) or {"answer": c.text}
    num = {str(i): a["model_id"] for i, a in enumerate(order, 1)}
    agreeing = [num[str(x)] for x in data.get("agreeing", []) or [] if str(x) in num]
    disputed = []
    for d in data.get("disputed", []) or []:
        if isinstance(d, dict):
            disputed.append({"point": str(d.get("point", "")),
                             "positions": {num.get(str(k), str(k)): v for k, v in (d.get("positions") or {}).items()}})
    verdict = Verdict(
        answer=str(data.get("answer") or c.text),
        confidence=as_float(data.get("confidence")),
        agreement=f"{len(agreeing)}/{len(answers)}" if agreeing else None,
        consensus=[str(x) for x in data.get("consensus", []) or []],
        disputed=disputed,
        minority_report=data.get("minority_report"),
        assumptions=[str(x) for x in data.get("assumptions", []) or []],
    )
    engine.emit(run, "phase_finished", phase=phase, model_id=judge.id, data={
        "judge": judge.id, "usage": c.usage.model_dump(), "agreeing": agreeing, "raw": c.text,
        "latency_s": round(time.perf_counter() - started, 2)})
    return verdict
