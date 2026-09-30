"""Protocol engine: run lifecycle, events, model calls, budget, cache. Presets live in their own modules."""

from __future__ import annotations

import hashlib
import os
import json
import re
import time
import uuid
from typing import Any

from .bus import EventBus
from .config import Config, ModelSpec
from .providers import Completion, ProviderError, complete, estimate_cost
from .schemas import RESOLVED_MODES, Event, Run, RunRequest, Verdict, now
from .store import RunStore

PROTOCOL_VERSION = "m3.0"  # bump when prompts/protocols change: invalidates the cache


class BudgetExceeded(RuntimeError):
    pass


class Engine:
    def __init__(self, config: Config, store: RunStore, bus: EventBus | None = None):
        self.config = config
        self.store = store
        self.bus = bus or EventBus()
        self._seq: dict[str, int] = {}
        self._reserved: dict[str, float] = {}  # estimated cost of in-flight calls per run (parallel calls)

    # ---------- public API ----------

    def create_run(self, req: RunRequest) -> Run:
        if os.environ.get("OBJECTION_NESTED"):
            raise ValueError("refused: a council cannot be started from inside the sandbox (recursion guard)")
        mode = req.mode or self.config.defaults.mode or "auto"
        if mode not in RESOLVED_MODES and mode != "auto":
            mode = "auto"
        if mode == "review" and not (req.target and req.target.strip()):
            raise ValueError("review needs `target` (a diff, plan or file content)")
        if mode == "code" and req.workdir and not os.path.isdir(req.workdir):
            raise ValueError(f"workdir does not exist: {req.workdir}")
        council = self.select_council(req.models)
        run = Run(
            id=uuid.uuid4().hex[:12],
            question=req.question,
            context=req.context,
            mode=mode,
            requested_mode=mode,
            tests_cmd=req.tests_cmd,
            workdir=req.workdir,
            solution_path=req.solution_path,
            check_facts=req.check_facts,
            source=req.source,
            target=req.target,
            target_kind=req.target_kind,
            fail_on=req.fail_on,
            models=[m.id for m in council],
            budget_usd=req.budget_usd if req.budget_usd is not None else self.config.defaults.budget_usd,
        )
        self.store.save_run(run)
        return run

    async def execute(self, run: Run) -> Run:
        from .router import route

        started = time.perf_counter()
        run.status = "running"
        self.store.save_run(run)
        self.emit(run, "run_started", data={"models": run.models, "mode": run.mode, "budget_usd": run.budget_usd})
        try:
            if run.mode == "auto":
                await route(self, run)
                self.store.save_run(run)
            run.verdict = await self.protocol(run.mode)(self, run)
            self.emit(run, "verdict", data=run.verdict.model_dump())
            run.status = "done"
        except Exception as exc:  # noqa: BLE001 — a run must always end with a terminal event
            run.status = "failed"
            run.error = str(exc)
            self.emit(run, "run_failed", data={"error": run.error})
        run.latency_s = round(time.perf_counter() - started, 2)
        run.finished_at = now()
        self.store.save_run(run)
        if run.status == "done":
            self.store.put_cache(self.cache_key(run), run.id)
            self.emit(run, "run_finished", data={"cost_usd": run.cost_usd, "latency_s": run.latency_s})
        return run

    @staticmethod
    def protocol(mode: str):
        from .code import run_code
        from .deliberate import run_deliberate
        from .quick import run_quick
        from .review import run_review
        from .verify import run_verify

        return {"review": run_review, "verify": run_verify, "quick": run_quick, "code": run_code}.get(mode, run_deliberate)

    async def ask(self, req: RunRequest) -> Run:
        """Create + execute, reusing a finished identical run from the cache unless `no_cache`."""
        run = self.create_run(req)
        if not req.no_cache and (hit := self.cached_for(run)):
            return hit
        return await self.execute(run)

    def cached_for(self, run: Run) -> Run | None:
        """If an identical finished run exists, drop the fresh `run` and return the cached one."""
        hit = self.store.get_cache(self.cache_key(run))
        cached = self.store.get_run(hit) if hit else None
        if cached and cached.status == "done":
            self.store.delete_run(run.id)
            cached.cached = True
            return cached
        return None

    def cache_key(self, run: Run) -> str:
        specs = sorted((m, self.config.model(m).model) for m in run.models)
        payload = [PROTOCOL_VERSION, run.requested_mode, run.question, run.context, run.target, run.target_kind,
                   run.fail_on, specs, run.tests_cmd, run.workdir, run.solution_path,
                   run.check_facts]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()

    # ---------- helpers for protocols ----------

    def should_check(self, run: Run, mode: str | None = None) -> bool:
        """Fact-check this run? Explicit request wins; otherwise the verifier config decides per mode."""
        if run.check_facts is not None:
            return run.check_facts
        v = self.config.verifier
        return v.enabled and (mode or run.mode) in v.modes

    async def call(self, run: Run, spec: ModelSpec, system: str, user: str, *, phase: str,
                   json_mode: bool = False) -> Completion | None:
        """One model call: accounts cost; on failure emits `model_error` and returns None.

        Budget manager: the call is skipped (model_error with budget=true) when its estimated cost would push the
        run over `budget_usd`. Protocols then degrade to a partial result instead of failing.
        """
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        est = estimate_cost(spec, system + user)
        reserved = self._reserved.get(run.id, 0.0)
        if run.cost_usd + reserved + est > run.budget_usd:
            run.budget_exhausted = True
            self.emit(run, "model_error", phase=phase, model_id=spec.id, data={
                "error": f"skipped: estimated ${est:.4f} would exceed the budget "
                         f"(${run.cost_usd:.4f} spent, ${reserved:.4f} in flight, budget ${run.budget_usd:.2f})",
                "budget": True})
            return None
        self._reserved[run.id] = reserved + est
        try:
            c = await complete(spec, messages, timeout_s=self.config.defaults.timeout_s, json_mode=json_mode)
        except ProviderError as exc:
            self.emit(run, "model_error", phase=phase, model_id=spec.id, data={"error": str(exc)})
            return None
        finally:
            self._reserved[run.id] = max(0.0, self._reserved.get(run.id, 0.0) - est)
        run.cost_usd += c.usage.cost_usd
        return c

    async def call_chair(self, run: Run, answered: list[str], system: str, user: str, *, phase: str) -> tuple[ModelSpec, Completion]:
        for spec in self.chair_candidates(run, answered):  # a failing chair must not sink the run
            c = await self.call(run, spec, system, user, phase=phase, json_mode=True)
            if c is not None:
                return spec, c
        if run.budget_exhausted:
            raise BudgetExceeded(f"budget ${run.budget_usd:.2f} exhausted before the chair could run")
        raise RuntimeError("no chair model could complete the task")

    def select_council(self, pinned: list[str] | None) -> list[ModelSpec]:
        ids = pinned or self.config.defaults.council.pinned
        if ids:
            return [self.config.model(i) for i in ids]
        pool = self.config.enabled_models
        if not pool:
            raise ValueError("the model pool is empty — add models to your config")
        return pool[: self.config.defaults.council.size]

    def chair_candidates(self, run: Run, answered: list[str]) -> list[ModelSpec]:
        """Configured chair first; otherwise a pool model outside the council, then council members that answered."""
        j = self.config.defaults.judge
        ids: list[str] = [j] if j != "auto" else []
        ids += [m.id for m in self.config.enabled_models if m.id not in run.models]
        ids += answered
        return [self.config.model(i) for i in dict.fromkeys(ids)]

    def check_budget(self, run: Run) -> None:
        if run.cost_usd > run.budget_usd or run.budget_exhausted:
            raise BudgetExceeded(f"budget exceeded: ${run.cost_usd:.4f} > ${run.budget_usd:.2f}")

    def emit(self, run: Run, type_: str, *, phase: str | None = None, model_id: str | None = None,
             data: dict | None = None) -> Event:
        seq = self._seq.get(run.id, 0) + 1
        self._seq[run.id] = seq
        ev = Event(run_id=run.id, seq=seq, type=type_, phase=phase, model_id=model_id, data=data or {})
        self.store.add_event(ev)
        self.bus.publish(ev)
        return ev


def parse_json(text: str) -> dict[str, Any]:
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else {"value": v}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def as_float(v: Any) -> float | None:
    try:
        return None if v is None else max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return None


def phase_cost(engine: "Engine", run: Run, phase: str) -> float:
    """Sum of `usage.cost_usd` over answer/critique events of one phase (used for saved-cost estimates)."""
    total = 0.0
    for e in engine.store.events(run.id):
        if e.phase == phase and e.type in ("answer", "critique"):
            total += float((e.data.get("usage") or {}).get("cost_usd") or 0.0)
    return total


def ctx_block(context: str | None) -> str:
    return f"\nContext:\n{context}\n" if context else ""


__all__ = ["Engine", "BudgetExceeded", "Verdict", "parse_json", "as_float", "ctx_block", "phase_cost", "PROTOCOL_VERSION"]
