"""Eval Harness (M4): is the council better than the obvious baselines on *your* pool, at the same budget?

For every task of a suite the harness runs:
- each preset under test (`verify`, `quick`, `deliberate` for answer tasks; `code` for coding tasks);
- every council model alone once (the "single" arm: one answer / one solution with its fix round).
From these it derives three baselines at equal budget (design.md §4.3):
- best single model — the model with the highest single accuracy on this suite (chosen with hindsight, which
  favours the baseline: if a preset still wins, the win is real);
- self-consistency of that model — k samples, majority vote (answers) or "any passes the tests" (code), where k is
  matched to the preset's mean cost (or its mean number of calls when the pool is free, e.g. local/mock models);
- simple vote — one answer from each council model, unweighted majority (answer tasks only).
Metrics: accuracy, abstentions, $ and seconds per task, right↔wrong flips after critique, calibration of the
council's confidence (Brier, ECE), preservation of a correct minority, error correlation and n_eff of the pool.
A preset that does not beat every baseline is flagged; `auto` mode then avoids it for this pool (defaults.respect_eval).

Every run of an eval is a normal run (source = "eval") labelled with the known answer, so it also feeds the per-model
statistics. Eval runs are hidden from the Web UI run list by default.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .engine import Engine
from .grading import expected_key, grade
from .labels import set_label
from .schemas import Run, RunRequest, now
from .store import RunStore
from .voting import normalize

ANSWER_PRESETS = ("verify", "quick", "deliberate")
BUILTIN = {"math-mini": "math-mini.jsonl", "code-mini": "code-mini.jsonl"}
MAX_SC = 9


@dataclass
class Task:
    id: str
    kind: str  # answer | code
    question: str
    expected: str | None = None
    context: str | None = None
    test: str | None = None  # code: python source that must run without errors (asserts)
    entry_point: str | None = None
    solution_path: str = "solution.py"


@dataclass
class Result:
    arm: str
    task: str
    run_id: str | None
    correct: bool
    abstained: bool = False
    cost_usd: float = 0.0
    latency_s: float = 0.0
    calls: int = 0
    answer: str | None = None
    key: str | None = None
    confidence: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------- suites ----------

def _from_row(i: int, d: dict[str, Any]) -> Task | None:
    if "entry_point" in d and ("test" in d or "tests" in d):  # HumanEval-style
        prompt = str(d.get("prompt") or "")
        ep = str(d["entry_point"])
        q = (f"Write the complete Python module solution.py that implements `{ep}`. Keep the signature.\n\n"
             f"```python\n{prompt}```")
        return Task(id=str(d.get("task_id") or d.get("id") or f"t{i}"), kind="code", question=q,
                    test=str(d.get("test") or d.get("tests")), entry_point=ep)
    if "task" in d and "test" in d:
        return Task(id=str(d.get("id") or f"t{i}"), kind="code", question=str(d["task"]), test=str(d["test"]),
                    entry_point=d.get("entry_point"), solution_path=str(d.get("solution_path") or "solution.py"))
    q = d.get("question") or d.get("problem") or d.get("input")
    a = d.get("expected", d.get("answer", d.get("target")))
    if q is None or a is None:
        return None
    return Task(id=str(d.get("id") or f"t{i}"), kind="answer", question=str(q), expected=str(a),
                context=d.get("context"))


def load_suite(name: str, store: RunStore | None = None, n: int | None = None) -> list[Task]:
    """Built-in suite name, `mine` (your labelled runs), or a .jsonl / .json file."""
    if name == "mine":
        tasks = mine(store) if store else []
    else:
        if name in BUILTIN:
            text = (files("objection") / "evals" / BUILTIN[name]).read_text(encoding="utf-8")
        else:
            p = Path(name).expanduser()
            if not p.exists():
                raise ValueError(f"unknown suite '{name}': use {', '.join(BUILTIN)}, mine, or a .jsonl file")
            text = p.read_text(encoding="utf-8")
        rows: list[dict[str, Any]]
        if text.lstrip().startswith("["):
            rows = json.loads(text)
        else:
            rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        tasks = [t for i, d in enumerate(rows, 1) if (t := _from_row(i, d))]
    if not tasks:
        raise ValueError(f"suite '{name}' has no usable tasks")
    return tasks[:n] if n else tasks


def mine(store: RunStore) -> list[Task]:
    """Your own labelled runs: questions with a known correct answer (`runs label <id> … --expected X`), or verify/quick
    runs labelled correct (then the verdict is the answer). Eval runs themselves are excluded."""
    tasks, seen = [], set()
    for r in store.list_all_runs(labeled_only=True):
        if r.source == "eval" or r.mode not in ("verify", "quick", "deliberate") or r.target:
            continue
        exp = r.expected
        if exp is None and r.label == "correct" and r.verdict and r.verdict.votes:
            exp = r.verdict.votes[0]["answer"]
        if exp is None or r.question in seen:
            continue
        seen.add(r.question)
        tasks.append(Task(id=f"run-{r.id}", kind="answer", question=r.question, expected=exp, context=r.context))
    return tasks


# ---------- running ----------

def pool_signature(config: Config, model_ids: list[str]) -> str:
    """Identifies the council an eval was measured on: ids and LiteLLM model strings (order-independent)."""
    specs = sorted(f"{m}={config.model(m).model}" for m in model_ids)
    return hashlib.sha256(json.dumps(specs).encode()).hexdigest()[:16]


class Harness:
    def __init__(self, config: Config, store: RunStore, *, presets: list[str] | None = None,
                 models: list[str] | None = None, check_facts: bool | None = False, budget_usd: float | None = None,
                 parallel: int = 2, sc_temperature: float = 0.7,
                 progress: Callable[[str], None] | None = None):
        self.config = config.model_copy(deep=True)
        self.config.defaults.preflight = False  # checked once for the whole eval instead
        self.store = store
        self.engine = Engine(self.config, store)
        self.presets = presets
        self.council = [m.id for m in self.engine.select_council(models)]
        self.check_facts = check_facts
        self.budget_usd = budget_usd
        self.sem = asyncio.Semaphore(max(1, parallel))
        self.sc_temperature = sc_temperature
        self.progress = progress or (lambda s: None)

    # one run → one result
    async def _run(self, task: Task, arm: str, mode: str, models: list[str], engine: Engine | None = None) -> Result:
        engine = engine or self.engine
        req = RunRequest(question=task.question, context=task.context, mode=mode, models=models, source="eval",
                         no_cache=True, budget_usd=self.budget_usd,
                         check_facts=(False if arm.startswith(("single", "sc")) else self.check_facts))
        workdir = None
        if task.kind == "code":
            workdir = _code_workdir(task)
            req.tests_cmd = f'"{sys.executable}" test_task.py'
            req.workdir, req.solution_path = str(workdir), task.solution_path
        async with self.sem:
            started = time.perf_counter()
            run = await engine.ask(req)
            latency = time.perf_counter() - started
        try:
            return self._result(task, arm, run, latency)
        finally:
            if workdir:
                import shutil

                shutil.rmtree(workdir.parent, ignore_errors=True)

    def _result(self, task: Task, arm: str, run: Run, latency: float) -> Result:
        v = run.verdict
        calls = sum(1 for e in self.store.events(run.id) if isinstance(e.data.get("usage"), dict))
        base = dict(arm=arm, task=task.id, run_id=run.id, cost_usd=run.cost_usd, latency_s=round(latency, 3),
                    calls=calls)
        if run.status != "done" or not v:
            return Result(correct=False, abstained=True, extra={"error": run.error}, **base)
        if task.kind == "code":
            ok = bool(v.solution and v.solution.get("passed"))
            return Result(correct=ok, abstained=v.verdict == "uncertain", **base)
        answer = v.votes[0]["answer"] if v.votes else v.answer
        abstained = v.verdict == "uncertain" or normalize(answer) == "unknown"
        ok = bool(grade(answer, task.expected)) and not abstained
        try:
            set_label(self.store, run.id, "correct" if ok else "wrong", expected=task.expected, config=self.config)
        except (KeyError, ValueError):
            pass
        return Result(correct=ok, abstained=abstained, answer=str(answer)[:300], key=normalize(answer)[:120],
                      confidence=v.confidence, extra=self._trajectory(task, run), **base)

    def _trajectory(self, task: Task, run: Run) -> dict[str, Any]:
        """Per-member first/final answers (for flips) and whether a correct answer survived in the votes."""
        rows = self.store.outcomes(run_id=run.id)
        members = {r["model_id"]: {"first": r["answer_key"], "final": r["final_key"]} for r in rows}
        v = run.verdict
        in_votes = any(grade(g["answer"], task.expected) for g in (v.votes if v else []))
        in_minority = bool(v and v.minority_report and grade(v.minority_report, task.expected))
        return {"members": members, "correct_in_votes": in_votes or in_minority}

    def _sc_engine(self, model_id: str) -> Engine:
        cfg = self.config.model_copy(deep=True)
        spec = cfg.model(model_id)
        if not float(spec.params.get("temperature") or 0) > 0:
            spec.params = {**spec.params, "temperature": self.sc_temperature}
        return Engine(cfg, self.store)

    async def run(self, tasks: list[Task], suite: str) -> dict[str, Any]:
        eval_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        kinds = {t.kind for t in tasks}
        presets = self.presets or (["code"] if kinds == {"code"} else ["verify", "quick"])
        report: dict[str, Any] = {"id": eval_id, "created_at": now().isoformat(), "suite": suite, "status": "running",
                                  "tasks": len(tasks), "council": self.council, "presets": presets,
                                  "pool": pool_signature(self.config, self.council),
                                  "models": {m: self.config.model(m).model for m in self.council}}
        self._save(report)
        excluded = await self._preflight()
        if excluded:
            report["excluded_models"] = excluded
            self.council = [m for m in self.council if m not in {e["id"] for e in excluded}]
            if not self.council:
                report["status"] = "failed"
                report["error"] = "no council model passed the health check"
                self._save(report)
                return report
            report["council"] = self.council
            report["pool"] = pool_signature(self.config, self.council)
        results: list[Result] = []

        async def one_task(t: Task) -> list[Result]:
            ps = [p for p in presets if (p == "code") == (t.kind == "code")]
            jobs = [self._run(t, p, p, self.council) for p in ps]
            jobs += [self._run(t, f"single:{m}", "code" if t.kind == "code" else "verify", [m]) for m in self.council]
            out = list(await asyncio.gather(*jobs))
            self.progress(f"{t.id}: " + "  ".join(f"{r.arm}={'✓' if r.correct else '·'}" for r in out))
            return out

        for chunk in await asyncio.gather(*(one_task(t) for t in tasks)):
            results += chunk
        # Self-consistency of the best single model, k matched to each preset's budget.
        singles = {m: [r for r in results if r.arm == f"single:{m}"] for m in self.council}
        best = max(self.council, key=lambda m: (_acc(singles[m]), -_mean(singles[m], "cost_usd")))
        ks = {p: _match_k([r for r in results if r.arm == p], singles[best]) for p in presets}
        k_max = max(ks.values(), default=1)
        sc_engine = self._sc_engine(best)
        by_task = {t.id: t for t in tasks}
        samples: dict[str, list[Result]] = {t.id: [r for r in singles[best] if r.task == t.id] for t in tasks}
        if k_max > 1:
            extra = await asyncio.gather(*(
                self._run(by_task[tid], f"sample:{best}",
                          "code" if by_task[tid].kind == "code" else "verify", [best], sc_engine)
                for tid in samples for _ in range(k_max - 1)))
            for r in extra:
                samples[r.task].append(r)
        report.update(build_report(tasks, presets, results, samples, best, ks, self.council))
        report["status"] = "done"
        report["latency_s"] = round(time.perf_counter() - started, 2)
        self._save(report)
        return report

    async def _preflight(self) -> list[dict[str, str]]:
        from .providers import probe

        specs = [self.config.model(m) for m in self.council]
        res = await asyncio.gather(*(probe(s, timeout_s=30, retries=self.config.defaults.retries) for s in specs))
        return [{"id": s.id, "reason": d[:300]} for s, (ok, d, _, _) in zip(specs, res) if not ok]

    def _save(self, report: dict[str, Any]) -> None:
        self.store.save_eval(report["id"], report["created_at"], report["suite"], report["pool"], report["status"],
                             json.dumps(report, ensure_ascii=False))


def _code_workdir(task: Task) -> Path:
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="objection-eval-")) / "project"
    root.mkdir()
    mod = Path(task.solution_path).stem
    ep = task.entry_point
    body = task.test or ""
    if ep:
        body = f"from {mod} import {ep}\n\n{body}\n\nif 'check' in globals():\n    check({ep})\n"
    (root / "test_task.py").write_text(body + "\nprint('ok')\n", encoding="utf-8")
    return root


# ---------- metrics ----------

def _acc(rs: list[Result]) -> float:
    return sum(r.correct for r in rs) / len(rs) if rs else 0.0


def _mean(rs: list[Result], attr: str) -> float:
    return sum(getattr(r, attr) for r in rs) / len(rs) if rs else 0.0


def _match_k(preset: list[Result], single: list[Result]) -> int:
    """Samples of the best model that cost what one preset run costs: by $ when priced, else by number of calls."""
    pc, sc = _mean(preset, "cost_usd"), _mean(single, "cost_usd")
    if pc > 0 and sc > 0:
        k = pc / sc
    else:
        k = _mean(preset, "calls") / max(1.0, _mean(single, "calls"))
    return max(1, min(MAX_SC, int(round(k))))


def _majority(keys: list[str | None]) -> str | None:
    c = Counter(k for k in keys if k and k != "unknown")
    if not c:
        return None
    top = c.most_common(2)
    if len(top) > 1 and top[0][1] == top[1][1]:
        return None  # tie → abstain
    return top[0][0]


def _arm(name: str, rows: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    n = len(rows)
    return {"arm": name, "n": n, "accuracy": round(sum(r["correct"] for r in rows) / n, 4) if n else None,
            "correct": sum(r["correct"] for r in rows), "abstained": sum(r.get("abstained", False) for r in rows),
            "cost_usd": round(sum(r["cost_usd"] for r in rows) / n, 6) if n else 0.0,
            "latency_s": round(sum(r["latency_s"] for r in rows) / n, 2) if n else 0.0,
            "calls": round(sum(r["calls"] for r in rows) / n, 2) if n else 0.0, **extra}


def _row(r: Result) -> dict[str, Any]:
    return {"task": r.task, "run_id": r.run_id, "correct": r.correct, "abstained": r.abstained,
            "cost_usd": r.cost_usd, "latency_s": r.latency_s, "calls": r.calls, "answer": r.answer}


def build_report(tasks: list[Task], presets: list[str], results: list[Result], samples: dict[str, list[Result]],
                 best: str, ks: dict[str, int], council: list[str]) -> dict[str, Any]:
    exp = {t.id: t.expected for t in tasks}
    kind = {t.id: t.kind for t in tasks}
    arms: dict[str, dict[str, Any]] = {}
    per_task: dict[str, dict[str, Any]] = {t.id: {"task": t.id, "expected": t.expected} for t in tasks}
    for m in council:
        rows = [_row(r) for r in results if r.arm == f"single:{m}"]
        arms[f"single:{m}"] = _arm(f"single:{m}", rows, model=m)
        for r in results:
            if r.arm == f"single:{m}":
                per_task[r.task][f"single:{m}"] = r.correct
    single_best = [r for r in results if r.arm == f"single:{best}"]
    arms["best_single"] = _arm("best_single", [_row(r) for r in single_best], model=best)
    answer_tasks = [t.id for t in tasks if t.kind == "answer"]
    if answer_tasks and len(council) >= 2:  # simple vote from the single answers
        rows = []
        for tid in answer_tasks:
            rs = [r for r in results if r.arm.startswith("single:") and r.task == tid]
            maj = _majority([r.key for r in rs])
            rows.append({"task": tid, "correct": bool(maj and grade(maj, exp[tid])), "abstained": maj is None,
                         "cost_usd": sum(r.cost_usd for r in rs), "latency_s": max((r.latency_s for r in rs), default=0),
                         "calls": sum(r.calls for r in rs)})
            per_task[tid]["vote"] = rows[-1]["correct"]
        arms["vote"] = _arm("vote", rows)
    presets_out: dict[str, Any] = {}
    for p in presets:
        pr = [r for r in results if r.arm == p]
        if not pr:
            continue
        k = ks.get(p, 1)
        sc_rows = []
        for r in pr:
            ss = samples.get(r.task, [])[:k]
            if kind[r.task] == "code":
                ok = any(s.correct for s in ss)
                ab = False
            else:
                maj = _majority([s.key for s in ss])
                ok, ab = bool(maj and grade(maj, exp[r.task])), maj is None
            sc_rows.append({"task": r.task, "correct": ok, "abstained": ab, "cost_usd": sum(s.cost_usd for s in ss),
                            "latency_s": max((s.latency_s for s in ss), default=0), "calls": sum(s.calls for s in ss)})
            per_task[r.task][p] = r.correct
            per_task[r.task][f"sc@{k}"] = ok
        arms[p] = _arm(p, [_row(r) for r in pr], preset=True, **_quality(pr, exp))
        arms[f"sc:{p}"] = _arm(f"self_consistency@{k}", sc_rows, model=best, k=k, matched_to=p)
        baselines = {"best_single": arms["best_single"]["accuracy"], "self_consistency": arms[f"sc:{p}"]["accuracy"]}
        if "vote" in arms and kind[pr[0].task] == "answer":
            baselines["vote"] = arms["vote"]["accuracy"]
        acc = arms[p]["accuracy"] or 0.0
        lost_to = [b for b, a in baselines.items() if a is not None and a >= acc]
        presets_out[p] = {"accuracy": acc, "baselines": baselines, "beats_baselines": not lost_to, "lost_to": lost_to,
                          "k": k, "significant": _significant(pr, single_best)}
    corr = error_correlation(results, council)
    return {"best_model": best, "arms": arms, "verdicts": presets_out, "correlation": corr,
            "per_task": list(per_task.values())}


def _quality(pr: list[Result], exp: dict[str, str | None]) -> dict[str, Any]:
    """Flips, calibration and minority preservation for one preset."""
    flips = {"right_to_wrong": 0, "wrong_to_right": 0}
    for r in pr:
        for m in (r.extra.get("members") or {}).values():
            a, b = m.get("first"), m.get("final")
            if a is None or b is None or a == b:
                continue
            ga, gb = bool(grade(a, exp[r.task])), bool(grade(b, exp[r.task]))
            if ga and not gb:
                flips["right_to_wrong"] += 1
            elif gb and not ga:
                flips["wrong_to_right"] += 1
    conf = [(r.confidence, r.correct) for r in pr if r.confidence is not None]
    brier = round(sum((c - float(ok)) ** 2 for c, ok in conf) / len(conf), 4) if conf else None
    ece = None
    if conf:
        bins: dict[int, list[tuple[float, bool]]] = {}
        for c, ok in conf:
            bins.setdefault(min(4, int(c * 5)), []).append((c, ok))
        ece = round(sum(len(b) / len(conf) * abs(sum(c for c, _ in b) / len(b) - sum(o for _, o in b) / len(b))
                        for b in bins.values()), 4)
    wrong_with_right_member = [r for r in pr if not r.correct and any(
        grade(m.get("first"), exp[r.task]) for m in (r.extra.get("members") or {}).values() if m.get("first"))]
    kept = sum(bool(r.extra.get("correct_in_votes")) for r in wrong_with_right_member)
    return {"flips": flips, "brier": brier, "ece": ece,
            "minority": {"cases": len(wrong_with_right_member), "preserved": kept}}


def _significant(pr: list[Result], best: list[Result]) -> dict[str, Any]:
    """Exact two-sided sign test on the tasks where the preset and the best single model disagree."""
    b = {r.task: r.correct for r in best}
    wins = sum(1 for r in pr if r.correct and not b.get(r.task, False))
    losses = sum(1 for r in pr if not r.correct and b.get(r.task, False))
    n = wins + losses
    if not n:
        return {"wins": 0, "losses": 0, "p_value": 1.0}
    tail = sum(math.comb(n, i) for i in range(0, min(wins, losses) + 1)) / 2 ** n
    return {"wins": wins, "losses": losses, "p_value": round(min(1.0, 2 * tail), 4)}


def error_correlation(results: list[Result], council: list[str]) -> dict[str, Any]:
    """Pairwise φ of the single models' error indicators and n_eff = k / (1 + (k − 1)·φ̄)."""
    err = {m: {r.task: not r.correct for r in results if r.arm == f"single:{m}"} for m in council}
    pairs: dict[str, float | None] = {}
    phis = []
    for i, a in enumerate(council):
        for b in council[i + 1:]:
            common = sorted(set(err[a]) & set(err[b]))
            phi = _phi([err[a][t] for t in common], [err[b][t] for t in common])
            pairs[f"{a}|{b}"] = phi
            if phi is not None:
                phis.append(phi)
    k = len(council)
    mean_phi = sum(phis) / len(phis) if phis else None
    n_eff = round(k / (1 + (k - 1) * max(0.0, mean_phi)), 2) if mean_phi is not None and k else None
    return {"phi": pairs, "mean_phi": round(mean_phi, 4) if mean_phi is not None else None, "n_eff": n_eff}


def _phi(x: list[bool], y: list[bool]) -> float | None:
    n11 = sum(a and b for a, b in zip(x, y))
    n10 = sum(a and not b for a, b in zip(x, y))
    n01 = sum(b and not a for a, b in zip(x, y))
    n00 = sum(not a and not b for a, b in zip(x, y))
    den = math.sqrt((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00))
    return round((n11 * n00 - n10 * n01) / den, 4) if den else None


# ---------- consumers ----------

def latest_verdicts(store: RunStore, pool: str) -> dict[str, dict[str, Any]]:
    """Newest finished eval of this pool → {preset: verdict}. Used by the router (defaults.respect_eval)."""
    out: dict[str, dict[str, Any]] = {}
    for body in store.list_evals(limit=20, pool=pool):
        rep = json.loads(body)
        if rep.get("status") != "done":
            continue
        for p, v in (rep.get("verdicts") or {}).items():
            out.setdefault(p, {**v, "eval_id": rep["id"]})
    return out


def to_markdown(rep: dict[str, Any]) -> str:
    lines = [f"# Eval {rep['id']} · {rep['suite']} · {rep.get('tasks')} tasks", "",
             f"Council: {', '.join(rep.get('council') or [])} · best single model: {rep.get('best_model')} · "
             f"status: {rep.get('status')}", ""]
    if rep.get("status") != "done":
        return "\n".join(lines + [rep.get("error") or ""])
    lines += ["| Arm | Accuracy | Abstained | $ / task | s / task | Calls |", "| --- | --- | --- | --- | --- | --- |"]
    for a in rep["arms"].values():
        acc = "—" if a["accuracy"] is None else f"{a['accuracy'] * 100:.1f}%"
        lines.append(f"| {a['arm']} | {acc} | {a['abstained']} | {a['cost_usd']:.5f} | {a['latency_s']:.1f} | "
                     f"{a['calls']:.1f} |")
    lines += ["", "## Verdicts", ""]
    for p, v in rep["verdicts"].items():
        mark = "✅ beats every baseline" if v["beats_baselines"] else f"⚠️ does not beat: {', '.join(v['lost_to'])}"
        sig = v.get("significant") or {}
        lines.append(f"- **{p}**: {v['accuracy'] * 100:.1f}% — {mark} (self-consistency k={v['k']}; vs best single: "
                     f"{sig.get('wins', 0)} wins / {sig.get('losses', 0)} losses, p={sig.get('p_value')})")
        q = rep["arms"].get(p, {})
        if q.get("flips"):
            lines.append(f"  flips right→wrong {q['flips']['right_to_wrong']}, wrong→right {q['flips']['wrong_to_right']}; "
                         f"Brier {q.get('brier')}, ECE {q.get('ece')}; correct minority kept "
                         f"{q['minority']['preserved']}/{q['minority']['cases']}")
    c = rep.get("correlation") or {}
    lines += ["", f"Error correlation: mean φ = {c.get('mean_phi')}, n_eff = {c.get('n_eff')} "
              f"of {len(rep.get('council') or [])} models"]
    return "\n".join(lines) + "\n"
