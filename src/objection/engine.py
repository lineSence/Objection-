"""Protocol engine. M0 ships the `deliberate` preset: independent answers → analysis + synthesis."""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
import uuid
from typing import Any

from .bus import EventBus
from .config import Config, ModelSpec
from .providers import ProviderError, complete
from .schemas import Event, Run, RunRequest, Verdict, now
from .store import RunStore

INDEPENDENT_SYSTEM = (
    "You are one member of a council of independent experts. Answer the question on your own; "
    "you will not see other answers. Start with a one-line position, then give your key arguments "
    "briefly. State assumptions explicitly. Answer in the language of the question."
)

SYNTH_SYSTEM = (
    "You are the chair of a council. You receive anonymous independent answers. Do not favour an answer "
    "because of its length, order or confidence; judge the arguments. Do not force consensus: keep real "
    "disagreements and the minority view. Reply with a single JSON object only."
)

SYNTH_TEMPLATE = """Question:
{question}
{context}
Independent answers:
{answers}

Return JSON with keys:
- "answer": the council's final answer (concise, actionable, in the language of the question)
- "confidence": number 0..1
- "agreeing": list of answer numbers that agree with the final answer
- "consensus": list of points all answers agree on
- "disputed": list of {{"point": str, "positions": {{"<answer number>": str}}}}
- "minority_report": the strongest dissenting view, or null
- "assumptions": list of assumptions made
"""


class BudgetExceeded(RuntimeError):
    pass


class Engine:
    def __init__(self, config: Config, store: RunStore, bus: EventBus | None = None):
        self.config = config
        self.store = store
        self.bus = bus or EventBus()
        self._seq: dict[str, int] = {}

    # ---------- public API ----------

    def create_run(self, req: RunRequest) -> Run:
        council = self._select_council(req.models)
        run = Run(
            id=uuid.uuid4().hex[:12],
            question=req.question,
            context=req.context,
            mode=req.mode,
            source=req.source,
            models=[m.id for m in council],
            budget_usd=req.budget_usd if req.budget_usd is not None else self.config.defaults.budget_usd,
        )
        self.store.save_run(run)
        return run

    async def execute(self, run: Run) -> Run:
        if run.mode != "deliberate":
            # Other presets arrive in M1–M2; until then they fall back to deliberate.
            pass
        started = time.perf_counter()
        run.status = "running"
        self.store.save_run(run)
        self._emit(run, "run_started", data={"models": run.models, "mode": run.mode, "budget_usd": run.budget_usd})
        try:
            answers = await self._independent(run)
            if not answers:
                raise RuntimeError("no model in the council produced an answer")
            run.verdict = await self._synthesize(run, answers)
            self._emit(run, "verdict", data=run.verdict.model_dump())
            run.status = "done"
        except Exception as exc:  # noqa: BLE001 — a run must always end with a terminal event
            run.status = "failed"
            run.error = str(exc)
            self._emit(run, "run_failed", data={"error": run.error})
        run.latency_s = round(time.perf_counter() - started, 2)
        run.finished_at = now()
        self.store.save_run(run)
        if run.status == "done":
            self._emit(run, "run_finished", data={"cost_usd": run.cost_usd, "latency_s": run.latency_s})
        return run

    async def ask(self, req: RunRequest) -> Run:
        return await self.execute(self.create_run(req))

    # ---------- phases ----------

    async def _independent(self, run: Run) -> list[dict[str, Any]]:
        phase = "independent"
        self._emit(run, "phase_started", phase=phase, data={"models": run.models})
        user = run.question if not run.context else f"{run.question}\n\nContext:\n{run.context}"
        messages = [{"role": "system", "content": INDEPENDENT_SYSTEM}, {"role": "user", "content": user}]
        started = time.perf_counter()

        async def one(spec: ModelSpec) -> dict[str, Any] | None:
            try:
                c = await complete(spec, messages, timeout_s=self.config.defaults.timeout_s)
            except ProviderError as exc:
                self._emit(run, "model_error", phase=phase, model_id=spec.id, data={"error": str(exc)})
                return None
            run.cost_usd += c.usage.cost_usd
            position = c.text.strip().splitlines()[0][:200] if c.text.strip() else ""
            self._emit(
                run, "answer", phase=phase, model_id=spec.id,
                data={"text": c.text, "position": position, "usage": c.usage.model_dump()},
            )
            return {"model_id": spec.id, "text": c.text}

        results = await asyncio.gather(*(one(self.config.model(m)) for m in run.models))
        answers = [r for r in results if r]
        self._emit(
            run, "phase_finished", phase=phase,
            data={"answered": len(answers), "failed": len(results) - len(answers),
                  "latency_s": round(time.perf_counter() - started, 2)},
        )
        self._check_budget(run)
        return answers

    async def _synthesize(self, run: Run, answers: list[dict[str, Any]]) -> Verdict:
        phase = "synthesize"
        candidates = self._judge_candidates(run, answers)
        self._emit(run, "phase_started", phase=phase, model_id=candidates[0].id, data={"judge": candidates[0].id})
        started = time.perf_counter()
        order = answers[:]
        if self.config.defaults.anonymize:
            random.shuffle(order)
        numbered = "\n\n".join(f"[{i}]\n{a['text']}" for i, a in enumerate(order, 1))
        prompt = SYNTH_TEMPLATE.format(
            question=run.question,
            context=f"\nContext:\n{run.context}\n" if run.context else "",
            answers=numbered,
        )
        messages = [{"role": "system", "content": SYNTH_SYSTEM}, {"role": "user", "content": prompt}]
        c = None
        for judge in candidates:  # a failing chair must not sink the whole run
            try:
                c = await complete(judge, messages, timeout_s=self.config.defaults.timeout_s, json_mode=True)
                break
            except ProviderError as exc:
                self._emit(run, "model_error", phase=phase, model_id=judge.id, data={"error": str(exc)})
        if c is None:
            raise RuntimeError("no chair model could synthesize the answers")
        run.cost_usd += c.usage.cost_usd
        data = _parse_json(c.text)
        num_to_model = {str(i): a["model_id"] for i, a in enumerate(order, 1)}
        agreeing = [num_to_model.get(str(n)) for n in data.get("agreeing", []) if str(n) in num_to_model]
        disputed = []
        for d in data.get("disputed", []) or []:
            if isinstance(d, dict):
                positions = {num_to_model.get(str(k), str(k)): v for k, v in (d.get("positions") or {}).items()}
                disputed.append({"point": d.get("point", ""), "positions": positions})
        verdict = Verdict(
            answer=str(data.get("answer") or c.text),
            confidence=_as_float(data.get("confidence")),
            agreement=f"{len(agreeing)}/{len(answers)}" if agreeing else None,
            consensus=[str(x) for x in data.get("consensus", []) or []],
            disputed=disputed,
            minority_report=data.get("minority_report"),
            assumptions=[str(x) for x in data.get("assumptions", []) or []],
        )
        self._emit(
            run, "phase_finished", phase=phase, model_id=judge.id,
            data={"judge": judge.id, "usage": c.usage.model_dump(), "agreeing": agreeing, "raw": c.text,
                  "latency_s": round(time.perf_counter() - started, 2)},
        )
        return verdict

    # ---------- helpers ----------

    def _select_council(self, pinned: list[str] | None) -> list[ModelSpec]:
        ids = pinned or self.config.defaults.council.pinned
        if ids:
            return [self.config.model(i) for i in ids]
        pool = self.config.enabled_models
        if not pool:
            raise ValueError("the model pool is empty — add models to your config")
        return pool[: self.config.defaults.council.size]

    def _judge_candidates(self, run: Run, answers: list[dict[str, Any]]) -> list[ModelSpec]:
        """Configured chair first; otherwise prefer a pool model outside the council, then council members."""
        j = self.config.defaults.judge
        answered = [a["model_id"] for a in answers]
        ids: list[str] = [j] if j != "auto" else []
        ids += [m.id for m in self.config.enabled_models if m.id not in run.models]
        ids += answered
        seen: list[str] = []
        for i in ids:
            if i not in seen:
                seen.append(i)
        return [self.config.model(i) for i in seen]

    def _check_budget(self, run: Run) -> None:
        if run.cost_usd > run.budget_usd:
            raise BudgetExceeded(f"budget exceeded: ${run.cost_usd:.4f} > ${run.budget_usd:.2f}")

    def _emit(self, run: Run, type_: str, *, phase: str | None = None, model_id: str | None = None,
              data: dict | None = None) -> Event:
        seq = self._seq.get(run.id, 0) + 1
        self._seq[run.id] = seq
        ev = Event(run_id=run.id, seq=seq, type=type_, phase=phase, model_id=model_id, data=data or {})
        self.store.add_event(ev)
        self.bus.publish(ev)
        return ev


def _parse_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {"answer": text}


def _as_float(v: Any) -> float | None:
    try:
        return None if v is None else max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return None
