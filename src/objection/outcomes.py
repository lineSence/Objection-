"""Per-model outcomes of a finished run (M3.1): the data behind the Model Registry, eval and error correlation.

One row per council member: its first and final answer (normalised), whether it agreed with the verdict, whether it
was right (when the run is labelled or the correct answer is known), fact-check hits against its claims, test
results, review findings, calls, errors, cost and latency. Rows are derived from the run's events, so they can be
rebuilt at any time (`objection runs reindex`) and are rewritten when a label changes.
"""

from __future__ import annotations

from typing import Any

from .grading import grade
from .schemas import Event, Run
from .store import RunStore
from .voting import normalize


def extract(run: Run, events: list[Event], families: dict[str, tuple[str, str]] | None = None) -> list[dict[str, Any]]:
    """families: model_id → (LiteLLM model string, family), from the config at the time of recording."""
    if run.status != "done" or not run.verdict:
        return []
    v = run.verdict
    families = families or {}
    members = list(dict.fromkeys(run.models))
    rows: dict[str, dict[str, Any]] = {}
    for m in members:
        model, fam = families.get(m, (None, None))
        rows[m] = {"run_id": run.id, "model_id": m, "model": model, "family": fam, "mode": run.mode,
                   "source": run.source, "created_at": run.created_at.isoformat(), "answer_key": None,
                   "final_key": None, "changed": None, "agreed": None, "correct": None, "claims_supported": 0,
                   "claims_refuted": 0, "tests_passed": None, "findings_reported": 0, "findings_confirmed": 0,
                   "findings_rejected": 0, "calls": 0, "errors": 0, "cost_usd": 0.0, "latency_s": 0.0}
    texts: dict[str, str] = {}
    agreeing: list[str] | None = None
    for e in events:
        r = rows.get(e.model_id or "")
        if e.type == "phase_finished" and e.phase == "synthesize" and isinstance(e.data.get("agreeing"), list):
            agreeing = [str(x) for x in e.data["agreeing"]]
        if r is None:
            continue
        usage = e.data.get("usage") if isinstance(e.data, dict) else None
        if isinstance(usage, dict):
            r["calls"] += 1
            r["cost_usd"] += float(usage.get("cost_usd") or 0.0)
            r["latency_s"] += float(usage.get("latency_s") or 0.0)
        if e.type == "model_error":
            r["errors"] += 1
        elif e.type == "answer" and e.phase == "independent":
            key = e.data.get("key")
            if run.mode in ("verify", "quick") and key is not None:
                r["answer_key"] = r["final_key"] = str(key)
            elif run.mode == "deliberate":
                texts[e.model_id] = str(e.data.get("text") or "")
                r["answer_key"] = r["final_key"] = normalize(e.data.get("position"))[:200]
            if run.mode == "review":
                r["findings_reported"] += len(e.data.get("findings") or [])
        elif e.type == "critique" and run.mode in ("verify", "quick") and e.data.get("key") is not None:
            r["final_key"] = str(e.data["key"])
            r["changed"] = int(bool(e.data.get("changed")))
        elif e.type == "critique" and run.mode == "deliberate":
            r["changed"] = int(bool(e.data.get("changed")))
            if e.data.get("position"):
                r["final_key"] = normalize(e.data["position"])[:200]
        elif e.type == "test_result":
            passed = bool(e.data.get("passed"))
            r["tests_passed"] = int(passed or bool(r["tests_passed"]))

    for c in v.claims:
        for m in c.authors:
            if m in rows:
                if c.status == "supported":
                    rows[m]["claims_supported"] += 1
                elif c.status == "refuted":
                    rows[m]["claims_refuted"] += 1
    for f in v.findings:
        for m in f.reported_by:
            if m in rows:
                rows[m]["findings_confirmed"] += f.status == "confirmed"
                rows[m]["findings_rejected"] += f.status == "rejected"

    winner = normalize(v.votes[0]["answer"]) if v.votes else None
    for m, r in rows.items():
        if run.mode in ("verify", "quick"):
            if r["final_key"] is not None and winner is not None:
                r["agreed"] = int(r["final_key"] == winner)
            if run.expected is not None and r["final_key"] is not None:
                r["correct"] = int(bool(grade(r["final_key"], run.expected)))
        elif run.mode == "deliberate":
            if agreeing is not None and m in texts:
                r["agreed"] = int(m in agreeing)
            if run.expected is not None and m in texts:
                r["correct"] = int(bool(grade(texts[m], run.expected)))
        elif run.mode == "code":
            sol = v.solution or {}
            if r["calls"]:
                r["agreed"] = int(sol.get("model_id") == m or bool(r["tests_passed"]))
            if r["tests_passed"] is not None:
                r["correct"] = r["tests_passed"]
        if r["correct"] is None and run.label and r["agreed"] is not None and run.mode != "review":
            # Only the verdict was judged: members that agreed share its fate; a dissenter of a wrong verdict
            # is not necessarily right, so it stays unknown.
            if run.label == "correct":
                r["correct"] = r["agreed"]
            elif r["agreed"]:
                r["correct"] = 0
        r["cost_usd"] = round(r["cost_usd"], 8)
        r["latency_s"] = round(r["latency_s"], 3)
    return list(rows.values())


def record(store: RunStore, run: Run, events: list[Event], config=None) -> list[dict[str, Any]]:
    fams: dict[str, tuple[str, str]] = {}
    if config is not None:
        fams = {m.id: (m.model, m.family_name) for m in config.models}
    else:
        fams = _families_from_events(events)
    rows = extract(run, events, fams)
    store.put_outcomes(run.id, rows)
    return rows


def _families_from_events(events: list[Event]) -> dict[str, tuple[str, str]]:
    for e in events:
        if e.type == "run_started" and isinstance(e.data.get("families"), dict):
            return {k: (str(v[0]), str(v[1])) for k, v in e.data["families"].items() if isinstance(v, list)}
    return {}


def reindex(store: RunStore, config=None) -> int:
    n = 0
    for run in store.list_all_runs():
        if run.status == "done":
            record(store, run, store.events(run.id), config)
            n += 1
    return n
