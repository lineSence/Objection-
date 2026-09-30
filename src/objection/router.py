"""`auto` mode: pick the cheapest protocol that fits the task (M2, D-014).

Rules first (they are free and unambiguous): a target → review, a tests command → code. Otherwise one short call to
the cheapest council model classifies the question; a keyword heuristic is the fallback when that call fails.
"""

from __future__ import annotations

import re

from . import prompts
from .engine import Engine, parse_json
from .providers import estimate_cost
from .schemas import Run

ROUTABLE = ("quick", "verify", "deliberate")

_CLAIM = re.compile(r"^(правда ли|верно ли|действительно ли|is it true|is it correct|true or false|fact[- ]check)"
                    r"|\b(claim|утверждени[ея])\b", re.I)
_OPEN = re.compile(r"\b(стоит ли|что лучше|как лучше|или|vs\.?|versus|should (i|we)|which is better|trade-?offs?|"
                   r"архитектур\w*|design|спроектир\w*|почему|why|how should|план|plan|pros|плюсы)\b", re.I)
_FACT = re.compile(r"^(сколько|когда|кто|где|какой|какая|какое|какие|в каком|what is|what's|who|when|where|"
                   r"how many|how much|which year|in what)\b", re.I)


def heuristic(task: str) -> tuple[str, str]:
    t = task.strip()
    if _CLAIM.search(t):
        return "verify", "checking a claim"
    if _OPEN.search(t) or len(t) > 280:
        return "deliberate", "open question or trade-off"
    if _FACT.search(t) and len(t) <= 200:
        return "quick", "short factual question"
    return "deliberate", "no clear single answer"


async def route(engine: Engine, run: Run) -> None:
    if run.target and run.target.strip():
        mode, reason, by = "review", "a target (diff/plan/file) was given", "rule"
    elif run.tests_cmd:
        mode, reason, by = "code", "a tests command was given", "rule"
    else:
        mode, reason, by = await classify(engine, run)
        mode, reason = respect_eval(engine, run, mode, reason)
    run.mode = mode  # type: ignore[assignment]
    run.route_reason = reason
    engine.emit(run, "route", phase="route", model_id=by if by not in ("rule", "heuristic") else None,
                data={"mode": mode, "reason": reason, "by": by, "escalated": False})


async def classify(engine: Engine, run: Run) -> tuple[str, str, str]:
    user = prompts.ROUTER_TEMPLATE.format(task=(run.question + ("\n" + run.context if run.context else ""))[:4000])
    specs = [engine.config.model(m) for m in run.models]
    for spec in sorted(specs, key=lambda s: estimate_cost(s, user))[:2]:  # cheapest first
        c = await engine.call(run, spec, prompts.ROUTER, user, phase="route", json_mode=True)
        if c is None:
            continue
        d = parse_json(c.text)
        mode = str(d.get("mode", "")).lower().strip()
        if mode in ROUTABLE:
            return mode, str(d.get("reason") or "")[:300], spec.id
    mode, reason = heuristic(run.question)
    return mode, f"heuristic: {reason}", "heuristic"


# A preset flagged by the latest eval of this council is replaced by the other short-answer preset when that one
# was not flagged. Open questions keep `deliberate` (no cheaper protocol fits them) but the reason says so.
_ALTERNATIVE = {"verify": "quick", "quick": "verify"}


def respect_eval(engine: Engine, run: Run, mode: str, reason: str) -> tuple[str, str]:
    if not engine.config.defaults.respect_eval:
        return mode, reason
    try:
        from .eval import latest_verdicts, pool_signature

        verdicts = latest_verdicts(engine.store, pool_signature(engine.config, run.models))
    except Exception:  # noqa: BLE001 — routing must not fail because of eval bookkeeping
        return mode, reason
    v = verdicts.get(mode)
    if not v or v.get("beats_baselines"):
        return mode, reason
    alt = _ALTERNATIVE.get(mode)
    note = f"eval {v.get('eval_id')}: {mode} did not beat {', '.join(v.get('lost_to') or ['a baseline'])}"
    if alt and verdicts.get(alt, {}).get("beats_baselines", alt not in verdicts):
        return alt, f"{reason}; {note} → {alt}"
    return mode, f"{reason}; warning — {note}"
