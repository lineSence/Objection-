"""Protocol engine: run lifecycle, events, model calls, budget, cache. Presets live in their own modules."""

from __future__ import annotations

import asyncio
import hashlib
import os
import json
import re
import time
import uuid
from typing import Any

from .bus import EventBus
from .config import Config, ModelSpec
from . import fingerprint
from .providers import Completion, ProviderError, complete, estimate_cost, probe
from .schemas import RESOLVED_MODES, Event, Run, RunRequest, Verdict, now
from .store import RunStore

PROTOCOL_VERSION = "m3.1"  # bump when prompts/protocols change: invalidates the cache


class BudgetExceeded(RuntimeError):
    pass


# Preflight health results shared by every engine in the process: (model string, api_base) → (ok, detail, monotonic ts)
_HEALTH: dict[tuple[str, str | None], tuple[bool, str, float]] = {}


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
        if req.workdir and not os.path.isdir(req.workdir):
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
            pinned=bool(req.models),
        )
        run.cache_key = self.cache_key(run)
        self.store.save_run(run)
        return run

    async def execute(self, run: Run) -> Run:
        from .router import route

        started = time.perf_counter()
        run.status = "running"
        self.store.save_run(run)
        self.emit(run, "run_started", data={
            "models": run.models, "mode": run.mode, "budget_usd": run.budget_usd,
            "families": {m.id: [m.model, m.family_name] for m in self.config.models
                         if m.id in run.models or m.enabled}})
        try:
            if self.config.defaults.preflight:
                await self.preflight(run)
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
            if not run.excluded_models:  # a result from a reduced council is not what an identical request expects
                self.store.put_cache(run.cache_key or self.cache_key(run), run.id)
            self.emit(run, "run_finished", data={"cost_usd": run.cost_usd, "latency_s": run.latency_s})
        try:
            from .outcomes import record

            record(self.store, run, self.store.events(run.id), self.config)
        except Exception:  # noqa: BLE001 — statistics must never break a run
            pass
        return run

    async def preflight(self, run: Run) -> None:
        """Health-check the council (cached for `health_ttl_s`); exclude models that do not respond.

        A council chosen by config is refilled from the rest of the pool; an explicitly pinned one is not.
        """
        ttl = self.config.defaults.health_ttl_s
        started = time.monotonic()

        async def check(spec: ModelSpec) -> tuple[bool, str]:
            key = (spec.model, spec.api_base)
            hit = _HEALTH.get(key)
            if hit and time.monotonic() - hit[2] < ttl:
                return hit[0], hit[1]
            ok, detail, _, c = await probe(spec, timeout_s=min(30.0, self.config.defaults.timeout_s),
                                           retries=self.config.defaults.retries,
                                           backoff_s=self.config.defaults.retry_backoff_s)
            if c is not None:
                run.cost_usd += c.usage.cost_usd
            _HEALTH[key] = (ok, detail, time.monotonic())
            return ok, detail

        async def sweep(ids: list[str]) -> list[str]:
            specs = [self.config.model(i) for i in ids]
            results = await asyncio.gather(*(check(s) for s in specs))
            good = []
            for spec, (ok, detail) in zip(specs, results):
                if ok:
                    good.append(spec.id)
                else:
                    run.excluded_models.append({"id": spec.id, "reason": detail[:300]})
                    self.emit(run, "model_error", phase="preflight", model_id=spec.id,
                              data={"error": f"excluded before the run: {detail[:300]}", "excluded": True})
            return good

        good = await sweep(run.models)
        if not run.pinned and len(good) < len(run.models):
            spare = [m.id for m in self.config.enabled_models
                     if m.id not in run.models and m.id not in {e["id"] for e in run.excluded_models}]
            need = len(run.models) - len(good)
            while need > 0 and spare:
                batch, spare = spare[:need], spare[need:]
                ok = await sweep(batch)
                good += ok
                need -= len(ok)
        if run.excluded_models:
            run.models = good
            self.store.save_run(run)
            self.emit(run, "phase_finished", phase="preflight", data={
                "models": good, "excluded": run.excluded_models,
                "latency_s": round(time.monotonic() - started, 2)})
        if not good:
            raise RuntimeError("no model in the council passed the health check: "
                               + "; ".join(f"{e['id']}: {e['reason']}" for e in run.excluded_models))

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
        """Everything the result depends on: inputs, council, the *contents* of workdir and the checking settings."""
        specs = sorted((m, self.config.model(m).model) for m in run.models)
        payload = [PROTOCOL_VERSION, run.requested_mode, run.question, run.context, run.target, run.target_kind,
                   run.fail_on, specs, run.tests_cmd, run.workdir,
                   fingerprint.directory(run.workdir) if self._reads_workdir(run) else None,
                   run.solution_path, run.check_facts, self.config.verifier.model_dump(),
                   self.config.sandbox.model_dump()]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()

    def _reads_workdir(self, run: Run) -> bool:
        """Does the result depend on the files in workdir? (code runs tests there; the Verifier may grep it)."""
        if not run.workdir:
            return False
        if run.requested_mode in ("code", "auto"):
            return True
        v = self.config.verifier
        if not (v.enabled and v.repo) or run.check_facts is False:
            return False
        return bool(run.check_facts) or run.requested_mode in v.modes

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
            c = await complete(spec, messages, timeout_s=self.config.defaults.timeout_s, json_mode=json_mode,
                               retries=self.config.defaults.retries, backoff_s=self.config.defaults.retry_backoff_s)
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
        council_families = {self.config.model(m).family_name for m in run.models if self._known(m)}
        outside = [m for m in self.config.enabled_models if m.id not in run.models
                   and m.id not in {e["id"] for e in run.excluded_models}]
        # A judge from another family first (no self-preference), then any model outside the council.
        ids += [m.id for m in outside if m.family_name not in council_families]
        ids += [m.id for m in outside]
        ids += answered
        return [self.config.model(i) for i in dict.fromkeys(ids)]

    def _known(self, model_id: str) -> bool:
        return any(m.id == model_id for m in self.config.models)

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
