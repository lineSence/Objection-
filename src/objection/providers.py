"""Model calls. Everything goes through LiteLLM (D-003); `mock/*` models run offline for tests and demos."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
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
_FLAKY: dict[str, int] = {}
_RESERVED = {"model", "messages", "timeout", "api_base", "api_key", "response_format", "stream"}


def _limit(spec: ModelSpec) -> asyncio.Semaphore | None:
    if not spec.max_parallel:
        return None
    key = spec.api_base or spec.model
    if key not in _semaphores:
        _semaphores[key] = asyncio.Semaphore(spec.max_parallel)
    return _semaphores[key]


EST_OUTPUT_TOKENS = 800


def _price(spec: ModelSpec, input_tokens: int, output_tokens: int) -> float | None:
    """Explicit per-model pricing (USD per 1M tokens) wins over LiteLLM's price list."""
    if spec.price_in is None and spec.price_out is None:
        return None
    return (input_tokens * (spec.price_in or 0.0) + output_tokens * (spec.price_out or 0.0)) / 1_000_000


def estimate_cost(spec: ModelSpec, prompt: str, output_tokens: int = EST_OUTPUT_TOKENS) -> float:
    """Rough pre-call estimate: ~3.5 chars per input token + a fixed output allowance. Unknown pricing → 0."""
    in_tok = int(len(prompt) / 3.5) + 20
    out_tok = int((spec.params or {}).get("max_tokens") or output_tokens)
    explicit = _price(spec, in_tok, out_tok)
    if explicit is not None:
        return explicit
    if spec.model.startswith("mock/") or spec.is_local:
        return 0.0
    try:
        import litellm

        p, c = litellm.cost_per_token(model=spec.model, prompt_tokens=in_tok, completion_tokens=out_tok)
        return float(p + c)
    except Exception:  # noqa: BLE001 — unknown model: cannot estimate, treat as free
        return 0.0


class TransientError(ProviderError):
    """Rate limit, 5xx or a dropped connection: worth retrying after a pause."""


_TRANSIENT_NAMES = {"RateLimitError", "APIConnectionError", "ServiceUnavailableError", "InternalServerError",
                    "BadGatewayError", "OverloadedError"}


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, TransientError):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status in (409, 429) or status >= 500):
        return True
    return type(exc).__name__ in _TRANSIENT_NAMES


async def complete(spec: ModelSpec, messages: list[dict], *, timeout_s: float, json_mode: bool = False,
                   retries: int = 0, backoff_s: float = 1.0) -> Completion:
    """Call `spec.model`; on transient errors retry with exponential backoff, then try `spec.fallbacks` in order.

    Timeouts are not retried (they would multiply the latency) but do move on to the next fallback.
    `usage.served_by` names the fallback that answered, if any.
    """
    sem = _limit(spec)
    targets = [spec.model] + [f for f in spec.fallbacks if f and f != spec.model]
    last: ProviderError | None = None
    for i, target in enumerate(targets):
        for attempt in range(retries + 1):
            try:
                if sem is None:
                    c = await _complete(spec, target, messages, timeout_s=timeout_s, json_mode=json_mode)
                else:
                    async with sem:
                        c = await _complete(spec, target, messages, timeout_s=timeout_s, json_mode=json_mode)
            except ProviderError as exc:
                last = exc
                if isinstance(exc, TransientError) and attempt < retries:
                    await asyncio.sleep(backoff_s * (2 ** attempt))
                    continue
                break
            if i:
                c.usage.served_by = target
            return c
    assert last is not None
    if len(targets) > 1:
        raise ProviderError(f"{last} (fallbacks tried: {', '.join(targets[1:])})")
    raise last


async def _complete(spec: ModelSpec, target: str, messages: list[dict], *, timeout_s: float, json_mode: bool) -> Completion:
    timeout = spec.timeout_s or timeout_s
    if target.startswith("mock/"):
        return await _mock(spec.model_copy(update={"model": target}), messages)
    import litellm  # imported lazily: heavy import

    litellm.suppress_debug_info = True
    litellm.drop_params = True  # e.g. response_format on providers that don't support it
    started = time.perf_counter()
    kwargs: dict = {k: v for k, v in spec.params.items() if k not in _RESERVED}
    kwargs.update(model=target, messages=messages, timeout=timeout)
    if spec.api_base:
        kwargs["api_base"] = spec.api_base
    if spec.api_key_env:
        key = os.environ.get(spec.api_key_env)
        if not key:
            raise ProviderError(f"{spec.id}: environment variable {spec.api_key_env} is not set")
        kwargs["api_key"] = key
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=timeout + 5)
    except Exception as exc:  # noqa: BLE001 — surface any provider failure uniformly
        msg = str(exc).replace("\n", " ")
        if len(msg) > 600:
            msg = msg[:600] + "…"
        err = TransientError if is_transient(exc) else ProviderError
        raise err(f"{spec.id}: {type(exc).__name__}: {msg}") from exc
    text = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    in_tok = getattr(usage, "prompt_tokens", 0) or 0
    out_tok = getattr(usage, "completion_tokens", 0) or 0
    cost = _price(spec, in_tok, out_tok)
    if cost is None:
        try:
            cost = float(litellm.completion_cost(completion_response=resp) or 0.0)
        except Exception:  # noqa: BLE001 — unknown pricing (e.g. local models) counts as free
            cost = 0.0
    return Completion(
        text=text,
        usage=Usage(
            input_tokens=in_tok,
            output_tokens=out_tok,
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
    if kind == "flaky":  # every other call fails with a retryable error
        _FLAKY[spec.id] = _FLAKY.get(spec.id, 0) + 1
        if _FLAKY[spec.id] % 2:
            raise TransientError(f"{spec.id}: mock rate limit (429)")
    if kind == "down":  # always a retryable error (tests fallbacks)
        raise TransientError(f"{spec.id}: mock 503")
    await asyncio.sleep(1.5 if kind == "slow" else 0.05)
    system = messages[0]["content"] if len(messages) > 1 else ""
    prompt = messages[-1]["content"]
    role = system.split("\n", 1)[0].removeprefix("ROLE: ").strip() if system.startswith("ROLE:") else "plain"
    h = int(hashlib.sha256((spec.id + prompt).encode()).hexdigest(), 16)
    text = _MOCK_ROLES.get(role, _mock_plain)(spec.id, prompt, h)
    in_tok, out_tok = len(prompt) // 4, len(text) // 4
    return Completion(text=text, usage=Usage(input_tokens=in_tok, output_tokens=out_tok,
                                             cost_usd=_price(spec, in_tok, out_tok) or 0.0, latency_s=0.05))


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


def _mock_router(model_id: str, prompt: str, h: int) -> str:
    from .router import heuristic

    task = prompt.split("TASK:", 1)[-1].split("\n\nReturn JSON", 1)[0].strip()
    mode, reason = heuristic(task)
    return json.dumps({"mode": mode, "reason": f"mock router: {reason}"})


def _mock_verifier(model_id: str, prompt: str, h: int) -> str:
    """Short canonical answers. Claims: mostly "true"; a question containing "спорн"/"disputed" splits the council."""
    q = prompt.lower()
    split = "спорн" in q or "disputed" in q
    if "true, false or unknown" in q or "claim:" in q:
        answer = ("false" if h % 2 else "true") if split else "true"
    elif any(w in q for w in ("сколько", "how many", "how much")):
        answer = ("42" if h % 3 else "41") if split else "42"
    else:
        answer = ("Вариант A" if h % 2 else "Вариант B") if split else "Вариант A"
    return json.dumps({"answer": answer, "reasoning": f"{model_id}: краткое обоснование (mock).",
                       "confidence": 0.9 if not split else 0.6}, ensure_ascii=False)


def _mock_verify_critic(model_id: str, prompt: str, h: int) -> str:
    own = prompt.split("YOUR_ANSWER:", 1)[1].split("\n", 2)[1].strip() if "YOUR_ANSWER:" in prompt else ""
    majority = "true" if "claim:" in prompt.lower() else "42" if "42" in prompt else "Вариант A"
    changed = own != majority and h % 2 == 0
    return json.dumps({"answer": majority if changed else own, "changed": changed,
                       "reason": "контрпример из ответа [1] опровергает мой довод" if changed else None,
                       "objections": [{"target": 1, "text": f"{model_id}: обоснование не проверяет граничный случай"}]},
                      ensure_ascii=False)


def _mock_coder(model_id: str, prompt: str, h: int) -> str:
    """Writes `add(a, b)`-style functions; half of the first-round candidates are buggy, fixes are correct."""
    import re

    fix = "TEST_OUTPUT:" in prompt
    m = re.search(r"SOLUTION_PATH:\s*(\S+)", prompt)
    filename = m.group(1) if m and m.group(1) != "-" else "solution.py"
    fn = re.search(r"\b([a-z_]\w*)\(\s*a\s*,\s*b\s*\)", prompt.split("TASK:", 1)[-1])
    name = fn.group(1) if fn else "add"
    buggy = not fix and h % 2 == 1
    body = "return a - b  # oops" if buggy else "return a + b"
    code = f'def {name}(a, b):\n    """Mock solution by {model_id}."""\n    {body}\n'
    return json.dumps({"filename": filename, "code": code,
                       "explanation": "исправлено по логам тестов" if fix else "простое решение"}, ensure_ascii=False)


def _mock_code_judge(model_id: str, prompt: str, h: int) -> str:
    return json.dumps({"best": 1, "reason": "самое простое и читаемое решение (mock)"}, ensure_ascii=False)


def _mock_claims(model_id: str, prompt: str, h: int) -> str:
    """Two claims from the final answer (one searched, one computed); one claim per candidate for vote plans;
    one repo claim per review finding (grep terms = the finding title)."""
    if "FINDINGS_TO_CHECK:" in prompt:
        block = prompt.split("FINDINGS_TO_CHECK:", 1)[1].split("\nFor EVERY finding", 1)[0]
        claims = []
        for ln in block.strip().splitlines():
            if not ln.strip().startswith("{"):
                continue
            f = json.loads(ln)
            repo = "REPOSITORY:" in prompt
            claims.append({"finding": f["id"], "text": f"проблема реальна: {f['title']}", "kind": "code",
                           "method": "repo" if repo else "none", "query": f'"{f["title"]}"',
                           "path": (f.get("location") or "").split(":", 1)[0] or None})
        return json.dumps({"claims": claims}, ensure_ascii=False)
    if "CANDIDATE_ANSWERS:" in prompt:
        cands = [ln.split("] ", 1)[1] for ln in prompt.split("CANDIDATE_ANSWERS:", 1)[1].split("\n\nFor EVERY", 1)[0]
                 .strip().splitlines() if "] " in ln]
        claims = []
        for i, c in enumerate(cands, 1):
            if c.strip().lstrip("-").isdigit():
                n = int(c)
                claims.append({"candidate": i, "text": f"ответ — {n}", "kind": "number", "method": "python",
                               "code": f"import json\nprint(json.dumps({{'holds': {n} == 42, 'value': {n}}}))"})
            else:
                claims.append({"candidate": i, "text": f"ответ — {c}", "kind": "fact", "method": "search", "query": c})
        return json.dumps({"claims": claims}, ensure_ascii=False)
    final = prompt.split("FINAL_ANSWER:", 1)[-1].split("\n\nMEMBER_ANSWERS:", 1)[0].strip()
    quote = final.split(".")[0].strip() if final else None
    return json.dumps({"claims": [
        {"text": quote or "утверждение", "quote": quote, "kind": "fact", "method": "search", "query": quote, "answers": [1, 2]},
        {"text": "2^10 = 1024", "quote": None, "kind": "number", "method": "python",
         "code": "import json\nprint(json.dumps({'holds': 2**10 == 1024, 'value': 2**10}))", "answers": [1]},
    ]}, ensure_ascii=False)


def _mock_judge(model_id: str, prompt: str, h: int) -> str:
    ev = prompt.split("EVIDENCE:", 1)[-1].lower()
    pro, con = ev.count("подтверждает") + ev.count("confirms"), ev.count("опровергает") + ev.count("refutes")
    status = "supported" if pro > con else "refuted" if con > pro else "unverified"
    return json.dumps({"status": status, "evidence": f"mock-судья {model_id}: источники {pro}+/{con}-", "sources": [1]},
                      ensure_ascii=False)


def _mock_reviser(model_id: str, prompt: str, h: int) -> str:
    return json.dumps({"answer": "Mock-синтез после фактчека: опровергнутое утверждение убрано, остальное без изменений.",
                       "changes": ["убрано опровергнутое утверждение"]}, ensure_ascii=False)


_MOCK_ROLES = {
    "claim-extractor": _mock_claims, "claim-judge": _mock_judge, "reviser": _mock_reviser,
    "router": _mock_router, "verifier": _mock_verifier, "verify-critic": _mock_verify_critic,
    "coder": _mock_coder, "code-judge": _mock_code_judge,
    "council-member": _mock_plain, "critic": _mock_critic, "chair": _mock_chair, "reviewer": _mock_reviewer,
    "review-dedupe": _mock_dedupe, "review-crosscheck": _mock_crosscheck,
}


async def probe(spec: ModelSpec, timeout_s: float = 30, retries: int = 0,
                backoff_s: float = 1.0) -> tuple[bool, str, float, Completion | None]:
    started = time.perf_counter()
    try:
        c = await complete(spec, [{"role": "user", "content": "Reply with: ok"}], timeout_s=timeout_s,
                           retries=retries, backoff_s=backoff_s)
    except ProviderError as exc:
        return False, str(exc), time.perf_counter() - started, None
    return True, "ok", time.perf_counter() - started, c


async def health_check(spec: ModelSpec, timeout_s: float = 30) -> tuple[bool, str, float]:
    ok, detail, lat, _ = await probe(spec, timeout_s)
    return ok, detail, lat
