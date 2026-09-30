import asyncio

from objection.engine import Engine
from objection.schemas import RunRequest


def run(engine, **kw):
    return asyncio.run(engine.ask(RunRequest(**kw)))


def test_deliberate_mock_run(config, store):
    engine = Engine(config, store)
    r = run(engine, question="SQLite или PostgreSQL?")
    assert r.status == "done"
    assert r.verdict and r.verdict.answer
    assert r.verdict.agreement == "2/3"
    types = [e.type for e in store.events(r.id)]
    assert types[0] == "run_started" and types[-1] == "run_finished"
    assert types.count("answer") == 3
    assert types.count("critique") == 3
    crit = [e for e in store.events(r.id) if e.type == "critique"]
    assert all(len(e.data["saw"]) == 2 and e.model_id not in e.data["saw"] for e in crit)
    assert store.get_run(r.id).status == "done"


def test_failing_model_is_reported_not_fatal(config, store):
    engine = Engine(config, store)
    r = run(engine, question="q", models=["a", "bad"])
    assert r.status == "done"
    errors = [e for e in store.events(r.id) if e.type == "model_error"]
    assert [e.model_id for e in errors] == ["bad"]


def test_all_models_fail(config, store):
    engine = Engine(config, store)
    r = run(engine, question="q", models=["bad"])
    assert r.status == "failed"
    assert store.events(r.id)[-1].type == "run_failed"


def test_cache_reuses_identical_run(config, store):
    engine = Engine(config, store)
    first = run(engine, question="same")
    second = run(engine, question="same")
    third = run(engine, question="same", no_cache=True)
    assert second.cached and second.id == first.id
    assert not third.cached and third.id != first.id
    assert len(store.list_runs()) == 2


def test_review_confirms_and_rejects(config, store):
    engine = Engine(config, store)
    r = run(engine, question="review", mode="review", target="diff --git a/x b/x\n+x = call()", models=["a", "b", "c"])
    v = r.verdict
    assert r.status == "done" and v.verdict == "fail"
    top = v.findings[0]
    assert top.title == "Missing error handling" and top.status == "confirmed"
    assert set(top.reported_by) == {"a", "b", "c"}  # merged duplicates
    assert all(f.status == "rejected" for f in v.findings[1:])  # low-severity noise refuted by cross-check
    types = [e.type for e in store.events(r.id)]
    assert types.count("critique") == 3


def test_review_threshold_passes_when_only_low(config, store):
    engine = Engine(config, store)
    r = run(engine, question="review", mode="review", target="+x", models=["a", "b", "c"], fail_on="critical")
    assert r.verdict.verdict == "pass"


def test_review_needs_target(config, store):
    engine = Engine(config, store)
    try:
        run(engine, question="review", mode="review")
    except ValueError:
        return
    raise AssertionError("expected ValueError")
