"""Model calls. Everything goes through LiteLLM (D-003); `mock/*` models run offline for tests and demos."""

from __future__ import annotations

import asyncio
import hashlib
import json
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
    """Deterministic offline model. `mock/fail` always raises, `mock/slow` sleeps.

    It recognises the `ROLE:` line of Objection!'s system prompts and returns plausible JSON for each role.
    """
    kind = spec.model.split("/", 1)[1]
    if kind == "fail":
        raise ProviderError(f"{spec.id}: mock failure")
    await asyncio.sleep(1.5 if kind == "slow" else 0.05)
    system = messages[0]["content"] if len(messages) > 1 else ""
    prompt = messages[-1]["content"]
    role = system.split("\n", 1)[0].removeprefix("ROLE: ").strip() if system.startswith("ROLE:") else "plain"
    h = int(hashlib.sha256((spec.id + prompt).encode()).hexdigest(), 16)
    text = _MOCK_ROLES.get(role, _mock_plain)(spec.id, prompt, h)
    return Completion(text=text, usage=Usage(input_tokens=len(prompt) // 4, output_tokens=len(text) // 4, latency_s=0.05))


def _mock_plain(model_id: str, prompt: str, h: int) -> str:
    if prompt.strip() == "Reply with: ok":
        return "ok"
    stance = ["Вариант A", "Вариант B"][h % 2]
    return f"{stance}.\nОтвет модели {model_id} (mock) на вопрос: {prompt[:120]}"


def _mock_critic(model_id: str, prompt: str, h: int) -> str:
    changed = h % 3 == 0
    return json.dumps({
        "position": "Вариант A — после критики" if changed else "Держу позицию",
        "changed": changed, "reason": "довод о нуле инфраструктуры" if changed else None,
        "objections": [{"target": 1, "text": f"{model_id}: аргумент не учитывает рост нагрузки"}],
        "supports": [{"target": 2, "text": "хороший довод про простоту"}] if "[2]" in prompt else [],
    }, ensure_ascii=False)


def _mock_chair(model_id: str, prompt: str, h: int) -> str:
    return json.dumps({
        "answer": "Mock-синтез: большинство склоняется к варианту A.", "confidence": 0.7, "agreeing": [1, 2],
        "consensus": ["Все модели ответили независимо"],
        "disputed": [{"point": "Выбор варианта", "positions": {}}],
        "minority_report": "Одна модель предпочла вариант B.", "assumptions": [],
    }, ensure_ascii=False)


def _mock_reviewer(model_id: str, prompt: str, h: int) -> str:
    findings = [{"title": "Missing error handling", "severity": "high", "location": "src/app.py:10",
                 "detail": "Exception from the network call is not handled.", "suggestion": "Wrap in try/except."}]
    if h % 2:
        findings.append({"title": f"Unclear naming ({model_id})", "severity": "low", "location": None,
                         "detail": "Variable names are vague.", "suggestion": None})
    return json.dumps({"findings": findings, "overall": "fail", "summary": f"{len(findings)} problems"})


def _mock_dedupe(model_id: str, prompt: str, h: int) -> str:
    items = json.loads(prompt.split("FINDINGS_JSON:", 1)[1].split("\n\nReturn JSON", 1)[0])
    groups: dict[str, dict] = {}
    for f in items:
        g = groups.setdefault(f["title"], {"members": [], "title": f["title"], "severity": f["severity"],
                                            "location": f.get("location"), "detail": f["detail"], "suggestion": None})
        g["members"].append(f["id"])
    return json.dumps({"groups": list(groups.values())})


def _mock_crosscheck(model_id: str, prompt: str, h: int) -> str:
    groups = json.loads(prompt.split("GROUPS_JSON:", 1)[1].split("\n\nReturn JSON", 1)[0])
    votes = [{"id": g["id"], "vote": "confirm" if g["severity"] in ("critical", "high") else "refute",
              "reason": "real problem" if g["severity"] in ("critical", "high") else "naming is fine here"}
             for g in groups]
    return json.dumps({"votes": votes})


_MOCK_ROLES = {
    "council-member": _mock_plain, "critic": _mock_critic, "chair": _mock_chair, "reviewer": _mock_reviewer,
    "review-dedupe": _mock_dedupe, "review-crosscheck": _mock_crosscheck,
}


async def health_check(spec: ModelSpec, timeout_s: float = 20) -> tuple[bool, str, float]:
    started = time.perf_counter()
    try:
        await complete(spec, [{"role": "user", "content": "Reply with: ok"}], timeout_s=timeout_s)
    except ProviderError as exc:
        return False, str(exc), time.perf_counter() - started
    return True, "ok", time.perf_counter() - started
