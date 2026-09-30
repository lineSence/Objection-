"""Model calls. Everything goes through LiteLLM (D-003); `mock/*` models run offline for tests and demos."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass

from .config import ModelSpec
from .schemas import Usage


@dataclass
class Completion:
    text: str
    usage: Usage


class ProviderError(RuntimeError):
    pass


_semaphores: dict[str, asyncio.Semaphore] = {}


def _limit(spec: ModelSpec) -> asyncio.Semaphore | None:
    if not spec.max_parallel:
        return None
    key = spec.api_base or spec.model
    if key not in _semaphores:
        _semaphores[key] = asyncio.Semaphore(spec.max_parallel)
    return _semaphores[key]


async def complete(spec: ModelSpec, messages: list[dict], *, timeout_s: float, json_mode: bool = False) -> Completion:
    sem = _limit(spec)
    if sem is None:
        return await _complete(spec, messages, timeout_s=timeout_s, json_mode=json_mode)
    async with sem:
        return await _complete(spec, messages, timeout_s=timeout_s, json_mode=json_mode)


async def _complete(spec: ModelSpec, messages: list[dict], *, timeout_s: float, json_mode: bool) -> Completion:
    timeout = spec.timeout_s or timeout_s
    if spec.model.startswith("mock/"):
        return await _mock(spec, messages)
    import litellm  # imported lazily: heavy import

    started = time.perf_counter()
    kwargs: dict = {"model": spec.model, "messages": messages, "timeout": timeout}
    if spec.api_base:
        kwargs["api_base"] = spec.api_base
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=timeout + 5)
    except Exception as exc:  # noqa: BLE001 — surface any provider failure uniformly
        raise ProviderError(f"{spec.id}: {type(exc).__name__}: {exc}") from exc
    text = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    try:
        cost = float(litellm.completion_cost(completion_response=resp) or 0.0)
    except Exception:  # noqa: BLE001 — unknown pricing (e.g. local models) counts as free
        cost = 0.0
    return Completion(
        text=text,
        usage=Usage(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            cost_usd=cost,
            latency_s=round(time.perf_counter() - started, 3),
        ),
    )


async def _mock(spec: ModelSpec, messages: list[dict]) -> Completion:
    """Deterministic offline model. `mock/fail` always raises, `mock/slow` sleeps."""
    kind = spec.model.split("/", 1)[1]
    if kind == "fail":
        raise ProviderError(f"{spec.id}: mock failure")
    await asyncio.sleep(1.5 if kind == "slow" else 0.05)
    prompt = messages[-1]["content"]
    digest = hashlib.sha256((spec.id + prompt).encode()).hexdigest()
    stance = ["Вариант A", "Вариант B"][int(digest[0], 16) % 2]
    if "JSON" in prompt and "consensus" in prompt:
        text = (
            '{"answer": "Mock-синтез: большинство склоняется к варианту A.", "confidence": 0.7, "agreeing": [1, 2], '
            '"consensus": ["Все модели ответили независимо"], '
            '"disputed": [{"point": "Выбор варианта", "positions": {}}], '
            '"minority_report": "Одна модель предпочла вариант B.", "assumptions": []}'
        )
    else:
        text = f"{stance}. Ответ модели {spec.id} (mock) на вопрос: {prompt[:120]}"
    tokens_in = len(prompt) // 4
    return Completion(text=text, usage=Usage(input_tokens=tokens_in, output_tokens=len(text) // 4, latency_s=0.05))


async def health_check(spec: ModelSpec, timeout_s: float = 20) -> tuple[bool, str, float]:
    started = time.perf_counter()
    try:
        await complete(spec, [{"role": "user", "content": "Reply with: ok"}], timeout_s=timeout_s)
    except ProviderError as exc:
        return False, str(exc), time.perf_counter() - started
    return True, "ok", time.perf_counter() - started
