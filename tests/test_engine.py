import asyncio

from objection.engine import Engine
from objection.schemas import RunRequest


def test_deliberate_mock_run(config, store):
    engine = Engine(config, store)
    run = asyncio.run(engine.ask(RunRequest(question="SQLite или PostgreSQL?")))
    assert run.status == "done"
    assert run.verdict and run.verdict.answer
    assert run.verdict.agreement == "2/3"
    types = [e.type for e in store.events(run.id)]
    assert types[0] == "run_started" and types[-1] == "run_finished"
    assert types.count("answer") == 3
    assert store.get_run(run.id).status == "done"


def test_failing_model_is_reported_not_fatal(config, store):
    engine = Engine(config, store)
    run = asyncio.run(engine.ask(RunRequest(question="q", models=["a", "bad"])))
    assert run.status == "done"
    errors = [e for e in store.events(run.id) if e.type == "model_error"]
    assert [e.model_id for e in errors] == ["bad"]


def test_all_models_fail(config, store):
    engine = Engine(config, store)
    run = asyncio.run(engine.ask(RunRequest(question="q", models=["bad"])))
    assert run.status == "failed"
    assert store.events(run.id)[-1].type == "run_failed"
