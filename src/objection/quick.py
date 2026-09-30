"""`quick`: two diverse models answer; if they agree we are done, otherwise escalate to `verify` with the full council.

Reuses the two answers on escalation, so disagreement costs no more than running `verify` directly.
"""

from __future__ import annotations

from .engine import Engine, phase_cost
from .schemas import Run, Verdict
from .verify import independent, run_verify, skip, verdict, vote


def pick_pair(engine: Engine, run: Run) -> list[str]:
    specs = [engine.config.model(m) for m in run.models]
    if len(specs) <= 2:
        return [s.id for s in specs]
    first = specs[0]
    other = next((s for s in specs[1:] if (s.provider, s.api_base) != (first.provider, first.api_base)), specs[1])
    return [first.id, other.id]


async def run_quick(engine: Engine, run: Run) -> Verdict:
    pair = pick_pair(engine, run)
    answers = await independent(engine, run, pair)
    if not answers:
        raise RuntimeError("no model produced an answer")
    t = vote(engine, run, answers, phase="vote")
    agree = t["unanimous"] and t["winner"]["key"] != "unknown"
    rest = [m for m in run.models if m not in pair]
    if agree or not rest:
        skip(engine, run, "two models agreed" if agree else "no more models to escalate to")
        v = verdict(engine, run, answers, t)
        v.stopped_early = agree
        if agree and answers:  # a full verify would also ask the remaining members
            per_answer = phase_cost(engine, run, "independent") / len(answers)
            v.saved_usd_est = round(per_answer * len(rest), 6)
        return v
    engine.emit(run, "route", phase="route", data={
        "mode": "verify", "reason": "the two models disagreed: asking the full council", "by": "quick",
        "escalated": True, "from": "quick"})
    v = await run_verify(engine, run, first=answers, models=run.models)
    v.escalated_to = "verify"
    return v
