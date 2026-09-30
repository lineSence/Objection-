"""M2: router, voting, verify (early stop / critique), quick escalation, code with real tests, budget manager."""

import asyncio
import sys

import pytest

from objection.config import Config, Defaults, ModelSpec
from objection.engine import Engine
from objection.router import heuristic
from objection.schemas import RunRequest
from objection.voting import normalize, tally


def run(engine, **kw):
    return asyncio.run(engine.ask(RunRequest(**kw)))


def types(store, r):
    return [e.type for e in store.events(r.id)]


@pytest.mark.parametrize("text,mode", [
    ("Правда ли, что Python 3.12 убрал distutils?", "verify"),
    ("Сколько байт в UUID?", "quick"),
    ("Что лучше для MVP: SQLite или PostgreSQL?", "deliberate"),
    ("How many bits are in an IPv6 address?", "quick"),
])
def test_heuristic(text, mode):
    assert heuristic(text)[0] == mode


def test_normalize_and_tally():
    assert normalize(" Yes. ") == normalize("да") == "true"
    assert normalize("42.0") == normalize("≈ 42") == "42"
    assert normalize("The Paris") == normalize("paris")
    t = tally([{"model_id": "a", "answer": "42", "confidence": 1.0},
               {"model_id": "b", "answer": "42.0", "confidence": 0.0},
               {"model_id": "c", "answer": "41", "confidence": 1.0}], {"a": 1, "b": 1, "c": 1})
    assert t["winner"]["key"] == "42" and t["agreement"] == "2/3" and not t["unanimous"]
    assert t["winner"]["weight"] == 1.5 and t["votes"][1]["weight"] == 1.0
    heavy = tally([{"model_id": "a", "answer": "x"}, {"model_id": "b", "answer": "y"}], {"a": 3, "b": 1})
    assert heavy["winner"]["answer"] == "x" and not heavy["tie"]


def test_auto_routes_and_records_reason(config, store):
    r = run(Engine(config, store), question="Сколько байт в UUID?")
    assert r.requested_mode == "auto" and r.mode == "quick" and r.route_reason
    route = [e for e in store.events(r.id) if e.type == "route"]
    assert route and route[0].data["mode"] == "quick" and route[0].model_id  # classified by a model
    r2 = run(Engine(config, store), question="x", target="diff --git a/x b/x\n+1", models=["a", "b"])
    assert r2.mode == "review"


def test_verify_unanimous_stops_early(config, store):
    r = run(Engine(config, store), question="Claim: 2+2=4\nIs this claim true? Answer exactly true, false or unknown.",
            mode="verify", models=["a", "b", "c"])
    v = r.verdict
    assert r.status == "done" and v.stopped_early and v.agreement == "3/3"
    assert normalize(v.votes[0]["answer"]) == "true"
    assert types(store, r).count("critique") == 0


def test_verify_disagreement_runs_critique(config, store):
    r = run(Engine(config, store), question="Сколько планет? (спорный вопрос)", mode="verify", models=["a", "b", "c"])
    t = types(store, r)
    if r.verdict.stopped_early:  # mock split depends on hashes; the pinned question must disagree
        pytest.fail("mock council unexpectedly unanimous")
    assert t.count("critique") == 3
    assert r.verdict.votes and r.verdict.agreement


def test_quick_agree_and_escalate(config, store):
    agree = run(Engine(config, store), question="Сколько байт в UUID?", mode="quick", models=["a", "b", "c"])
    assert agree.verdict.stopped_early and types(store, agree).count("answer") == 2
    esc = run(Engine(config, store), question="Claim: спорно\nIs this claim true? Answer exactly true, false or unknown.",
              mode="quick", models=["a", "b", "c"])
    events = store.events(esc.id)
    answers = [e for e in events if e.type == "answer"]
    if esc.verdict.escalated_to:
        assert esc.verdict.escalated_to == "verify"
        assert len(answers) == 3  # the two first answers are reused, only the third model is asked
        assert any(e.type == "route" and e.data.get("escalated") for e in events)
    else:
        assert esc.verdict.stopped_early


@pytest.fixture
def project(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "test_solution.py").write_text("from solution import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    return d


def test_code_tests_decide(config, store, project):
    cmd = f"{sys.executable} -m pytest -q -p no:cacheprovider"
    r = run(Engine(config, store), question="Write add(a, b) in solution.py", mode="code", tests_cmd=cmd,
            workdir=str(project), solution_path="solution.py", models=["a", "b", "c"])
    v = r.verdict
    assert r.status == "done" and v.verdict == "pass", v.answer
    assert v.solution["passed"] and "a + b" in v.solution["code"]
    assert any(e.type == "test_result" for e in store.events(r.id))
    assert not (project / "solution.py").exists()  # the real workdir is never touched


def test_code_auto_routes_with_tests(config, store, project):
    r = run(Engine(config, store), question="Write add(a, b)", tests_cmd=f"{sys.executable} -m pytest -q -p no:cacheprovider",
            workdir=str(project), solution_path="solution.py", models=["a", "b"])
    assert r.mode == "code" and r.verdict.verdict == "pass"


def test_code_without_tests_uses_chair(config, store):
    r = run(Engine(config, store), question="Write add(a, b)", mode="code", models=["a", "b"])
    assert r.verdict.solution and r.verdict.solution["passed"] is None and len(r.verdict.candidates) == 2


def test_budget_manager_degrades(store):
    priced = [ModelSpec(id=i, model=f"mock/{i}", price_in=1000.0, price_out=1000.0) for i in ("a", "b", "c")]
    cfg = Config(models=priced, defaults=Defaults(budget_usd=2.0))
    r = run(Engine(cfg, store), question="Что лучше: A или B?", mode="deliberate")
    assert r.status == "done" and r.budget_exhausted
    assert r.verdict.verdict == "uncertain"
    skipped = [e for e in store.events(r.id) if e.type == "model_error" and e.data.get("budget")]
    assert skipped
    assert r.cost_usd <= 2.0 + 1.0  # at most the estimate error of one call over


def test_mcp_result_for_verify(config, store):
    from objection.mcp_server import _result

    r = run(Engine(config, store), question="Claim: x\nIs this claim true? Answer exactly true, false or unknown.",
            mode="verify", models=["a", "b"])
    out = _result(r)
    assert out["mode"] == "verify" and out["votes"] and out.get("stopped_early")


def test_code_fix_round_after_all_fail(config, store, project, monkeypatch):
    import json

    from objection import providers

    def always_buggy_first(model_id, prompt, h):
        fixed = "TEST_OUTPUT:" in prompt
        body = "return a + b" if fixed else "return a * b"
        return json.dumps({"filename": "solution.py", "code": f"def add(a, b):\n    {body}\n", "explanation": "x"})

    monkeypatch.setitem(providers._MOCK_ROLES, "coder", always_buggy_first)
    r = run(Engine(config, store), question="Write add(a, b)", mode="code", models=["a", "b"],
            tests_cmd=f"{sys.executable} -m pytest -q -p no:cacheprovider", workdir=str(project), solution_path="solution.py")
    v = r.verdict
    assert v.verdict == "pass" and v.solution["round"] == 2
    phases = [e.phase for e in store.events(r.id) if e.type == "phase_started"]
    assert phases[-3:] == ["test", "fix", "retest"]
    fails = [e for e in store.events(r.id) if e.type == "test_result" and not e.data["passed"]]
    assert len(fails) == 2 and "assert" in fails[0].data["output"]
