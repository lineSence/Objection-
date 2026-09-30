"""M3.1: cache fingerprints, preflight, retries/fallbacks, families, outcomes, labels, repo evidence, review fact-check."""

import asyncio
import sys
import time

import pytest
from typer.testing import CliRunner

import objection.engine as E
from objection import repo
from objection.config import Config, Defaults, ModelSpec, VerifierConfig, infer_family, load_config
from objection.engine import Engine
from objection.grading import grade
from objection.labels import set_label
from objection.schemas import RunRequest


def run(engine, **kw):
    return asyncio.run(engine.ask(RunRequest(**kw)))


@pytest.fixture(autouse=True)
def fresh_health():
    E._HEALTH.clear()
    yield
    E._HEALTH.clear()


# ---------- cache ----------

def test_code_cache_follows_workdir_contents(config, store, tmp_path):
    proj = tmp_path / "proj"  # not tmp_path itself: the store's SQLite file lives there
    proj.mkdir()
    (proj / "test_x.py").write_text("from solution import add\nassert add(2, 3) == 5\n")
    engine = Engine(config, store)
    kw = dict(question="write add(a, b)", mode="code", tests_cmd=f'"{sys.executable}" test_x.py', workdir=str(proj),
              solution_path="solution.py")
    first = run(engine, **kw)
    assert run(engine, **kw).cached  # nothing changed → cached
    (proj / "test_x.py").write_text("from solution import add\nassert add(2, 2) == 4\n")
    third = run(engine, **kw)
    assert not third.cached and third.id != first.id


def test_cache_follows_verifier_settings(config, store):
    engine = Engine(config, store)
    first = run(engine, question="same question")
    assert run(engine, question="same question").cached
    engine.config.verifier.max_claims = 2
    assert not run(engine, question="same question").cached
    assert first.cache_key


# ---------- preflight ----------

def test_preflight_refills_council_from_pool(store):
    cfg = Config(models=[ModelSpec(id="down", model="mock/fail"), ModelSpec(id="a", model="mock/a"),
                         ModelSpec(id="b", model="mock/b"), ModelSpec(id="c", model="mock/c")],
                 defaults=Defaults())
    r = run(Engine(cfg, store), question="q")
    assert r.status == "done"
    assert r.models == ["a", "b", "c"]
    assert [e["id"] for e in r.excluded_models] == ["down"]
    pre = [e for e in store.events(r.id) if e.phase == "preflight" and e.type == "model_error"]
    assert pre and pre[0].data["excluded"]
    assert store.get_cache(r.cache_key) is None  # reduced council → not cached


def test_preflight_keeps_pinned_council(config, store):
    r = run(Engine(config, store), question="q", models=["a", "bad"])
    assert r.models == ["a"] and r.pinned


def test_preflight_can_be_disabled(config, store):
    config.defaults.preflight = False
    r = run(Engine(config, store), question="q", models=["a", "bad"])
    assert r.models == ["a", "bad"] and not r.excluded_models


# ---------- retries and fallbacks ----------

def test_retry_on_transient_error(store):
    cfg = Config(models=[ModelSpec(id="f1", model="mock/flaky"), ModelSpec(id="a", model="mock/a")],
                 defaults=Defaults(retry_backoff_s=0))
    r = run(Engine(cfg, store), question="q", models=["f1", "a"])
    assert r.status == "done" and not r.excluded_models
    assert not [e for e in store.events(r.id) if e.type == "model_error"]


def test_no_retries_means_failure(store):
    cfg = Config(models=[ModelSpec(id="f2", model="mock/down"), ModelSpec(id="a", model="mock/a")],
                 defaults=Defaults(retries=0, preflight=False))
    r = run(Engine(cfg, store), question="q", mode="deliberate", models=["f2", "a"])
    assert [e.model_id for e in store.events(r.id) if e.type == "model_error"] == ["f2"]


def test_fallback_answers_and_is_recorded(store):
    cfg = Config(models=[ModelSpec(id="d", model="mock/down", fallbacks=["mock/backup"]),
                         ModelSpec(id="a", model="mock/a")], defaults=Defaults(retry_backoff_s=0))
    r = run(Engine(cfg, store), question="q", models=["d", "a"])
    answers = [e for e in store.events(r.id) if e.type == "answer" and e.model_id == "d"]
    assert answers and answers[0].data["usage"]["served_by"] == "mock/backup"


# ---------- families ----------

@pytest.mark.parametrize("model,family", [
    ("openai/gpt-4o-mini", "openai"), ("openrouter/anthropic/claude-3.5-sonnet", "anthropic"),
    ("ollama/qwen2.5-coder:7b", "alibaba"), ("groq/llama3-70b", "meta"), ("gemini/gemini-2.5-pro", "google"),
    ("hosted_vllm/my-model", "hosted_vllm"),
])
def test_infer_family(model, family):
    assert infer_family(model) == family
    assert ModelSpec(id="x", model=model, family="Custom").family_name == "custom"


def test_chair_prefers_other_family(store):
    cfg = Config(models=[ModelSpec(id=i, model=f"mock/{i}", family="f1") for i in ("a", "b", "c")]
                 + [ModelSpec(id="same", model="mock/s", family="f1"), ModelSpec(id="other", model="mock/o", family="f2")])
    engine = Engine(cfg, store)
    run_ = engine.create_run(RunRequest(question="q"))
    assert [m.id for m in engine.chair_candidates(run_, ["a"])][:2] == ["other", "same"]


# ---------- outcomes and labels ----------

def test_outcomes_recorded_for_verify(config, store):
    r = run(Engine(config, store), question="Сколько будет спорный ответ?", mode="verify")
    rows = store.outcomes(run_id=r.id)
    assert {x["model_id"] for x in rows} == set(r.models)
    assert all(x["answer_key"] in ("41", "42") and x["agreed"] in (0, 1) for x in rows)
    assert all(x["family"] == "mock" and x["calls"] >= 1 for x in rows)
    assert all(x["correct"] is None for x in rows)


def test_label_with_expected_grades_every_model(config, store):
    r = run(Engine(config, store), question="Сколько будет спорный ответ?", mode="verify")
    labelled = set_label(store, r.id, None, expected="42", config=config)
    rows = {x["model_id"]: x for x in store.outcomes(run_id=r.id)}
    assert labelled.label == ("correct" if r.verdict.votes[0]["answer"] == "42" else "wrong")
    assert all(x["correct"] == int(x["final_key"] == "42") for x in rows.values())
    set_label(store, r.id, None)
    assert store.get_run(r.id).label is None


def test_label_api_and_reindex(config, store):
    from fastapi.testclient import TestClient

    from objection.outcomes import reindex
    from objection.server import create_app

    with TestClient(create_app(config, store)) as client:
        rid = client.post("/api/runs", json={"question": "how many?", "mode": "quick"}).json()["id"]
        for _ in range(100):
            if client.get(f"/api/runs/{rid}").json()["status"] == "done":
                break
            time.sleep(0.05)
        _label_checks(client, rid, config, store, reindex)


def _label_checks(client, rid, config, store, reindex):
    assert client.post(f"/api/runs/{rid}/label", json={"label": "correct"}).json()["label"] == "correct"
    assert client.post(f"/api/runs/{rid}/label", json={"label": "nope"}).status_code == 400
    rows = client.get(f"/api/outcomes?run_id={rid}").json()
    assert rows and all(r["correct"] == 1 for r in rows if r["answer_key"])  # quick: the 3rd member was not asked
    assert any(r["correct"] is None for r in rows)
    store._db.execute("DELETE FROM model_outcomes")
    assert reindex(store, config) >= 1 and store.outcomes(run_id=rid)


def test_grade():
    assert grade("The answer is 1,116 books.", "1116")
    assert grade("42", "reasoning #### 42")
    assert not grade("41 or maybe 42?", "41") is False  # first line contains the number
    assert grade("true", "True") and not grade("false, because…", "true")
    assert grade("Используйте PostgreSQL.", "postgresql")
    assert grade("x", None) is None


# ---------- repository evidence ----------

def test_repo_search_finds_identifiers(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "config.py").write_text("import yaml\n\ndef parse_config(path):\n    return yaml.safe_load(open(path))\n")
    (tmp_path / "README.md").write_text("nothing here\n")
    (tmp_path / "blob.bin").write_bytes(b"\0\0parse_config")
    hits = repo.search(tmp_path, "parse_config does not catch yaml.YAMLError", path_hint="src/config.py:3")
    assert hits and hits[0]["path"] == "src/config.py" and hits[0]["line"] == 3
    assert all(h["path"] != "blob.bin" for h in hits)
    assert repo.search(tmp_path, "") == [] and repo.search(None, "x") == []


def _review_project(tmp_path, word):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(f"x = call()  # Missing error handling — this {word} the finding\n")
    return str(tmp_path)


@pytest.mark.parametrize("word,status,verdict", [("confirms", "supported", "fail"), ("refutes", "refuted", "pass")])
def test_review_findings_checked_against_repo(config, store, tmp_path, word, status, verdict):
    wd = _review_project(tmp_path, word)
    r = run(Engine(config, store), question="review", mode="review", target="+x = call()", models=["a", "b", "c"],
            check_facts=True, workdir=wd)
    v = r.verdict
    top = next(f for f in v.findings if f.title == "Missing error handling")
    assert top.evidence == status and top.claim_id
    assert v.verdict == verdict
    claim = next(c for c in v.claims if c.id == top.claim_id)
    assert claim.method == "repo" and claim.sources[0]["path"] == "src/app.py"
    if status == "refuted":
        assert top.status == "rejected" and any(o.model_id == "verifier" for o in top.refuted_by)
        assert v.fact_override and "confirmed → rejected" in v.fact_override
    else:
        assert v.fact_override is None  # evidence agreed with the votes: nothing was overturned
    tools = [e for e in store.events(r.id) if e.type == "tool_call"]
    assert tools and tools[0].data["tool"] == "repo"


def test_review_without_check_is_unchanged(config, store, tmp_path):
    r = run(Engine(config, store), question="review", mode="review", target="+x", models=["a", "b", "c"],
            workdir=_review_project(tmp_path, "refutes"))
    assert r.verdict.verdict == "fail" and not r.verdict.claims


def test_verifier_repo_can_be_disabled(config, store, tmp_path):
    config.verifier = VerifierConfig(repo=False, web_search=False)
    r = run(Engine(config, store), question="review", mode="review", target="+x", models=["a", "b", "c"],
            check_facts=True, workdir=_review_project(tmp_path, "refutes"))
    assert all(c.method != "repo" for c in r.verdict.claims)
    assert r.verdict.verdict == "fail"  # nothing could be checked: votes decide


# ---------- CLI: models add / remove, runs label ----------

def test_models_add_remove(tmp_path, monkeypatch):
    from objection.cli import app

    cfg = tmp_path / "objection.yaml"
    monkeypatch.setenv("OBJECTION_CONFIG", str(cfg))
    cli = CliRunner()
    r = cli.invoke(app, ["models", "add", "gpt", "openai/gpt-4o-mini", "--fallback", "openrouter/openai/gpt-4o-mini"])
    assert r.exit_code == 0, r.output
    assert "family openai" in r.output
    m = load_config().models
    assert [x.id for x in m] == ["gpt"] and m[0].fallbacks == ["openrouter/openai/gpt-4o-mini"]
    assert cli.invoke(app, ["models", "add", "gpt", "openai/gpt-4o"]).exit_code == 1
    assert cli.invoke(app, ["models", "add", "gpt", "openai/gpt-4o", "--replace"]).exit_code == 0
    assert load_config().models[0].model == "openai/gpt-4o"
    assert cli.invoke(app, ["models", "remove", "gpt"]).exit_code == 0
    assert load_config().models == []
    assert cli.invoke(app, ["models", "remove", "nope"]).exit_code == 1
