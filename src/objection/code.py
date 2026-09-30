"""`code`: candidate solutions from every member → run the project's tests on each → one fix round → winner.

The tests decide, not a model (D-014). Without a tests command a chair picks the best candidate.
WARNING: model-written code is executed locally in a temporary copy of `workdir`; a real sandbox is planned for M3.
"""

from __future__ import annotations

import asyncio
import os
import random
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from . import prompts
from .engine import BudgetExceeded, Engine, ctx_block, parse_json
from .schemas import Run, Verdict

IGNORE = shutil.ignore_patterns(".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
                                "dist", "build", "*.egg-info")
TEST_TIMEOUT_S = float(os.environ.get("OBJECTION_TEST_TIMEOUT_S", "120"))
OUTPUT_TAIL = 4000
MAX_FILE_LIST = 60


def _safe_rel(path: str) -> str | None:
    p = Path(path.strip().lstrip("/\\"))
    if not p.parts or ".." in p.parts or p.is_absolute():
        return None
    return p.as_posix()


def project_listing(workdir: str | None) -> str:
    if not workdir:
        return ""
    files: list[str] = []
    for root, dirs, names in os.walk(workdir):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache"}]
        for n in names:
            files.append(os.path.relpath(os.path.join(root, n), workdir))
            if len(files) >= MAX_FILE_LIST:
                break
    return "Project files:\n" + "\n".join(sorted(files)) + "\n"


def run_tests(workdir: str | None, filename: str, code: str, tests_cmd: str) -> dict[str, Any]:
    """Copy the project to a temp dir, write the candidate file, run the tests. Never touches `workdir` itself."""
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="objection-code-") as tmp:
        root = Path(tmp) / "project"
        if workdir:
            shutil.copytree(workdir, root, ignore=IGNORE, symlinks=True)
        else:
            root.mkdir()
        target = root / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        try:
            p = subprocess.run(tests_cmd, shell=True, cwd=root, capture_output=True, text=True,
                               timeout=TEST_TIMEOUT_S, env=env)
            out, code_ = (p.stdout + ("\n" + p.stderr if p.stderr else "")).strip(), p.returncode
        except subprocess.TimeoutExpired as exc:
            out = f"timeout after {TEST_TIMEOUT_S:.0f}s\n{(exc.stdout or '')!s}"[-OUTPUT_TAIL:]
            code_ = -1
    return {"passed": code_ == 0, "exit_code": code_, "output": out[-OUTPUT_TAIL:],
            "duration_s": round(time.perf_counter() - started, 2)}


async def run_code(engine: Engine, run: Run) -> Verdict:
    cands = await propose(engine, run)
    if not cands:
        raise RuntimeError("no model produced a candidate solution")
    if not run.tests_cmd:
        return await judge(engine, run, cands)
    await test_all(engine, run, cands, phase="test")
    if not any(c["passed"] for c in cands) and not run.budget_exhausted:
        failed = [c for c in cands if not c["passed"]]
        try:
            fixed = await fix(engine, run, failed)
        except BudgetExceeded:
            fixed = []
        if fixed:
            await test_all(engine, run, fixed, phase="retest")
            cands += fixed
    return finish(run, cands)


async def propose(engine: Engine, run: Run) -> list[dict[str, Any]]:
    phase = "independent"
    engine.emit(run, "phase_started", phase=phase, data={"models": run.models})
    started = time.perf_counter()
    user = prompts.CODER_TEMPLATE.format(task=run.question, context=ctx_block(run.context), path=run.solution_path or "-",
                                         tests=run.tests_cmd or "-", project=project_listing(run.workdir))

    async def one(model_id: str) -> dict[str, Any] | None:
        c = await engine.call(run, engine.config.model(model_id), prompts.CODER, user, phase=phase, json_mode=True)
        return None if c is None else _candidate(engine, run, model_id, c, phase, round_=1)

    results = await asyncio.gather(*(one(m) for m in run.models))
    cands = [r for r in results if r]
    engine.emit(run, "phase_finished", phase=phase, data={
        "answered": len(cands), "failed": len(results) - len(cands), "latency_s": round(time.perf_counter() - started, 2)})
    return cands


def _candidate(engine: Engine, run: Run, model_id: str, c, phase: str, round_: int) -> dict[str, Any] | None:
    d = parse_json(c.text)
    code = d.get("code")
    if not isinstance(code, str) or not code.strip():
        engine.emit(run, "model_error", phase=phase, model_id=model_id, data={"error": "no `code` in the reply"})
        return None
    filename = _safe_rel(run.solution_path or str(d.get("filename") or "")) or "solution.py"
    cand = {"model_id": model_id, "filename": filename, "code": code, "explanation": str(d.get("explanation") or ""),
            "round": round_, "passed": None, "output": ""}
    engine.emit(run, "answer", phase=phase, model_id=model_id, data={
        "text": cand["explanation"], "position": f"{filename} · {len(code.splitlines())} строк", "filename": filename,
        "code": code, "round": round_, "usage": c.usage.model_dump()})
    return cand


async def test_all(engine: Engine, run: Run, cands: list[dict[str, Any]], *, phase: str) -> None:
    engine.emit(run, "phase_started", phase=phase, data={"tests_cmd": run.tests_cmd, "candidates": len(cands)})
    started = time.perf_counter()

    async def one(c: dict[str, Any]) -> None:
        r = await asyncio.to_thread(run_tests, run.workdir, c["filename"], c["code"], run.tests_cmd or "")
        c.update(passed=r["passed"], output=r["output"], exit_code=r["exit_code"])
        engine.emit(run, "test_result", phase=phase, model_id=c["model_id"], data=dict(r, round=c["round"],
                                                                                        filename=c["filename"]))

    await asyncio.gather(*(one(c) for c in cands))
    engine.emit(run, "phase_finished", phase=phase, data={
        "passed": [c["model_id"] for c in cands if c["passed"]], "latency_s": round(time.perf_counter() - started, 2)})


async def fix(engine: Engine, run: Run, failed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phase = "fix"
    engine.emit(run, "phase_started", phase=phase, data={"models": [c["model_id"] for c in failed]})
    started = time.perf_counter()

    async def one(prev: dict[str, Any]) -> dict[str, Any] | None:
        user = prompts.CODER_FIX_TEMPLATE.format(
            task=run.question, context=ctx_block(run.context), path=prev["filename"], tests=run.tests_cmd,
            project=project_listing(run.workdir), code=prev["code"], output=prev["output"][-3000:])
        c = await engine.call(run, engine.config.model(prev["model_id"]), prompts.CODER, user, phase=phase, json_mode=True)
        return None if c is None else _candidate(engine, run, prev["model_id"], c, phase, round_=2)

    results = [r for r in await asyncio.gather(*(one(c) for c in failed)) if r]
    engine.emit(run, "phase_finished", phase=phase, data={"answered": len(results),
                                                          "latency_s": round(time.perf_counter() - started, 2)})
    return results


def _public(c: dict[str, Any]) -> dict[str, Any]:
    return {k: c.get(k) for k in ("model_id", "filename", "round", "passed", "explanation", "output", "code")}


def finish(run: Run, cands: list[dict[str, Any]]) -> Verdict:
    passed = [c for c in cands if c["passed"]]
    # Prefer first-round passes (independent solutions), then the shortest file: simpler wins among equals.
    best = min(passed, key=lambda c: (c["round"], len(c["code"]))) if passed else cands[-1]
    authors = sorted({c["model_id"] for c in passed})
    ok = bool(passed)
    note = " (budget exhausted: fix round skipped)" if run.budget_exhausted and not ok else ""
    return Verdict(
        answer=(f"Tests pass: solution by {best['model_id']} → {best['filename']}. "
                f"{len(authors)}/{len({c['model_id'] for c in cands})} models produced a passing solution."
                if ok else f"No candidate passed `{run.tests_cmd}` after a fix round{note}. "
                           f"Last attempt by {best['model_id']} is attached with the test output."),
        verdict="pass" if ok else ("uncertain" if run.budget_exhausted else "fail"),
        agreement=f"{len(authors)}/{len({c['model_id'] for c in cands})}",
        solution={"model_id": best["model_id"], "filename": best["filename"], "code": best["code"],
                  "passed": bool(best["passed"]), "output": best["output"], "round": best["round"]},
        candidates=[_public(c) for c in cands],
    )


async def judge(engine: Engine, run: Run, cands: list[dict[str, Any]]) -> Verdict:
    phase = "synthesize"
    started = time.perf_counter()
    answered = [c["model_id"] for c in cands]
    engine.emit(run, "phase_started", phase=phase, data={"judge": engine.chair_candidates(run, answered)[0].id})
    order = cands[:]
    random.shuffle(order)
    blocks = "\n\n".join(f"[{i}] {c['filename']}\n{c['code'][:12000]}" for i, c in enumerate(order, 1))
    user = prompts.CODE_JUDGE_TEMPLATE.format(task=run.question, context=ctx_block(run.context), candidates=blocks)
    try:
        j, c = await engine.call_chair(run, answered, prompts.CODE_JUDGE, user, phase=phase)
        d = parse_json(c.text)
        try:
            best = order[int(d.get("best")) - 1]
        except (TypeError, ValueError, IndexError):
            best = order[0]
        reason, judge_id = str(d.get("reason") or ""), j.id
    except BudgetExceeded:
        best, reason, judge_id = cands[0], "budget exhausted: first candidate returned untested", None
    engine.emit(run, "phase_finished", phase=phase, model_id=judge_id, data={
        "judge": judge_id, "best": best["model_id"], "reason": reason, "latency_s": round(time.perf_counter() - started, 2)})
    return Verdict(
        answer=f"No tests command: the chair picked the solution by {best['model_id']} → {best['filename']}. {reason}",
        verdict="uncertain" if judge_id is None else None,
        solution={"model_id": best["model_id"], "filename": best["filename"], "code": best["code"], "passed": None,
                  "output": "", "round": 1},
        candidates=[_public(c) for c in cands],
        minority_report=None,
    )
