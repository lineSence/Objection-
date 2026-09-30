"""Human labels for finished runs (M3.1): the cheapest way to build your own eval set (design.md §8)."""

from __future__ import annotations

from .outcomes import record
from .schemas import Run, now
from .store import RunStore

LABELS = ("correct", "wrong")


def set_label(store: RunStore, run_id: str, label: str | None, *, expected: str | None = None,
              note: str | None = None, config=None) -> Run:
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(f"run not found: {run_id}")
    if run.status != "done":
        raise ValueError("only finished runs can be labelled")
    if label is not None and label not in LABELS:
        raise ValueError("label must be correct, wrong or clear")
    if label is None and expected is None:
        run.label = run.expected = run.label_note = run.labeled_at = None
    else:
        if expected is not None and label is None and run.verdict:
            from .grading import grade

            ok = grade(run.verdict.votes[0]["answer"] if run.verdict.votes else run.verdict.answer, expected)
            label = None if ok is None else ("correct" if ok else "wrong")
        run.label = label  # type: ignore[assignment]
        run.expected = expected if expected is not None else run.expected
        run.label_note = note if note is not None else run.label_note
        run.labeled_at = now()
    store.save_run(run)
    record(store, run, store.events(run.id), config)
    return run
