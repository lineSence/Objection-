"""M4: Eval Harness — suites, arms, equal-budget baselines, metrics, stored reports, eval-aware routing."""

import asyncio
import json

from typer.testing import CliRunner

from objection.engine import Engine
from objection.eval import Harness, _match_k, _phi, error_correlation, load_suite, pool_signature, to_markdown, Result
from objection.schemas import RunRequest


def write_suite(tmp_path):
    rows = [{"id": "t1", "question": "How many apples are there?", "expected": "42"},
            {"id": "t2", "question": "How many pears (disputed)?", "expected": "42"},
            {"question": "How many plums are there?", "answer": "reasoning … #### 7"}]  # GSM8K format
    p = tmp_path / "suite.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return p


def test_load_suites(tmp_path, store):
    assert len(load_suite("math-mini")) == 30 and len(load_suite("code-mini")) == 8
    assert load_suite("code-mini")[0].kind == "code" and load_suite("code-mini")[0].entry_point
    tasks = load_suite(str(write_suite(tmp_path)), n=2)
    assert [t.id for t in tasks] == ["t1", "t2"]
    assert load_suite(str(write_suite(tmp_path)))[2].expected.endswith("#### 7")


def test_eval_answer_suite(config, store, tmp_path):
    tasks = load_suite(str(write_suite(tmp_path)))
    rep = asyncio.run(Harness(config, store, parallel=4).run(tasks, "suite"))
    assert rep["status"] == "done" and rep["tasks"] == 3
    arms = rep["arms"]
    assert {"best_single", "vote", "verify", "quick", "sc:verify", "sc:quick"} <= set(arms)
    assert arms["single:a"]["n"] == 3 and arms["verify"]["n"] == 3
    assert arms["sc:verify"]["k"] >= 2  # free models: k matched by the number of calls
    assert set(rep["verdicts"]) == {"verify", "quick"}
    assert all(isinstance(v["beats_baselines"], bool) for v in rep["verdicts"].values())
    assert "flips" in arms["verify"] and "brier" in arms["verify"]
    t3 = next(r for r in rep["per_task"] if r["task"] == "t3")
    assert t3["verify"] is False and t3["single:a"] is False  # everyone says 42, the answer is 7
    # stored, labelled, hidden from the UI list
    assert json.loads(store.get_eval(rep["id"]))["status"] == "done"
    runs = store.list_all_runs()
    assert runs and all(r.source == "eval" for r in runs)
    assert all(r.label in ("correct", "wrong") for r in runs if r.status == "done" and r.mode != "code")
    assert store.list_runs(include_eval=False) == []
    assert "beats every baseline" in to_markdown(rep) or "does not beat" in to_markdown(rep)


def test_eval_code_suite(config, store, tmp_path):
    p = tmp_path / "code.jsonl"
    p.write_text(json.dumps({"task_id": "add", "entry_point": "add",
                             "prompt": "def add(a, b):\n    \"\"\"Return a + b.\"\"\"\n",
                             "test": "def check(f):\n    assert f(2, 3) == 5\n"}))
    rep = asyncio.run(Harness(config, store).run(load_suite(str(p)), "code"))
    assert rep["status"] == "done" and rep["presets"] == ["code"]
    assert rep["arms"]["code"]["accuracy"] == 1.0  # tests pick a passing candidate (or the fix round repairs it)
    assert "vote" not in rep["arms"]


def test_match_k_and_correlation():
    mk = lambda cost, calls: Result(arm="x", task="t", run_id=None, correct=True, cost_usd=cost, calls=calls)
    assert _match_k([mk(0.03, 3)], [mk(0.01, 1)]) == 3
    assert _match_k([mk(0, 7)], [mk(0, 1)]) == 7
    assert _match_k([mk(1, 7)], [mk(0.001, 1)]) == 9  # capped
    assert _phi([True, True, False, False], [True, True, False, False]) == 1.0
    assert _phi([True, False], [True, True]) is None
    res = [Result(arm=f"single:{m}", task=t, run_id=None, correct=ok) for m, t, ok in
           [("a", "1", True), ("a", "2", False), ("a", "3", True), ("b", "1", True), ("b", "2", False), ("b", "3", True)]]
    c = error_correlation(res, ["a", "b"])
    assert c["mean_phi"] == 1.0 and c["n_eff"] == 1.0


def test_router_respects_eval(config, store):
    engine = Engine(config, store)
    council = [m.id for m in engine.select_council(None)]
    body = {"id": "ev1", "created_at": "2026-10-01T00:00:00+00:00", "suite": "s", "status": "done",
            "pool": pool_signature(config, council),
            "verdicts": {"verify": {"beats_baselines": False, "lost_to": ["best_single"]},
                         "quick": {"beats_baselines": True, "lost_to": []}}}
    store.save_eval("ev1", body["created_at"], "s", body["pool"], "done", json.dumps(body))
    r = asyncio.run(engine.ask(RunRequest(question="Is it true that the claim holds?")))
    assert r.mode == "quick" and "ev1" in r.route_reason
    config.defaults.respect_eval = False
    r2 = asyncio.run(engine.ask(RunRequest(question="Is it true that the claim holds, really?")))
    assert r2.mode == "verify"


def test_eval_cli(config, store, tmp_path, monkeypatch):
    import yaml

    from objection.cli import app

    cfg = tmp_path / "objection.yaml"
    cfg.write_text(yaml.safe_dump({"models": [{"id": i, "model": f"mock/{i}"} for i in "abc"],
                                   "storage": {"path": str(tmp_path / "runs.sqlite")}}))
    monkeypatch.setenv("OBJECTION_CONFIG", str(cfg))
    cli = CliRunner()
    r = cli.invoke(app, ["eval", "run", "--suite", str(write_suite(tmp_path)), "--presets", "verify", "-f", "json"])
    assert r.exit_code in (0, 1), r.output
    rep = json.loads(r.output)
    assert list(rep["verdicts"]) == ["verify"]
    listed = cli.invoke(app, ["eval", "list"])
    assert rep["id"] in listed.output
    assert "# Eval" in cli.invoke(app, ["eval", "show", rep["id"]]).output
    assert cli.invoke(app, ["eval", "run", "--presets", "nope"]).exit_code == 3


def test_mine_suite_from_labels(config, store):
    from objection.labels import set_label

    engine = Engine(config, store)
    r = asyncio.run(engine.ask(RunRequest(question="How many moons?", mode="verify")))
    r2 = asyncio.run(engine.ask(RunRequest(question="How many rings?", mode="quick")))
    set_label(store, r.id, None, expected="42")
    set_label(store, r2.id, "correct")
    tasks = load_suite("mine", store)
    assert {t.question: t.expected for t in tasks} == {"How many moons?": "42", "How many rings?": "42"}
