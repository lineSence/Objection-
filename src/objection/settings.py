"""Web UI settings: model pool (LiteLLM), provider API keys, council defaults. Mounted by server.create_app."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import Config, Defaults, ModelSpec, config_path, read_secrets, save_config, write_secrets, SECRETS_FILE
from .providers import probe

# Presets for the "add model" form. `prefix` is the LiteLLM provider prefix; `env` the standard key variable.
PROVIDERS: list[dict[str, Any]] = [
    {"id": "openai", "label": "OpenAI", "prefix": "openai", "env": "OPENAI_API_KEY", "catalog": "openai"},
    {"id": "anthropic", "label": "Anthropic", "prefix": "anthropic", "env": "ANTHROPIC_API_KEY", "catalog": "anthropic"},
    {"id": "gemini", "label": "Google Gemini", "prefix": "gemini", "env": "GEMINI_API_KEY", "catalog": "gemini"},
    {"id": "openrouter", "label": "OpenRouter", "prefix": "openrouter", "env": "OPENROUTER_API_KEY", "catalog": "openrouter"},
    {"id": "deepseek", "label": "DeepSeek", "prefix": "deepseek", "env": "DEEPSEEK_API_KEY", "catalog": "deepseek"},
    {"id": "mistral", "label": "Mistral", "prefix": "mistral", "env": "MISTRAL_API_KEY", "catalog": "mistral"},
    {"id": "groq", "label": "Groq", "prefix": "groq", "env": "GROQ_API_KEY", "catalog": "groq"},
    {"id": "xai", "label": "xAI", "prefix": "xai", "env": "XAI_API_KEY", "catalog": "xai"},
    {"id": "ollama", "label": "Ollama (локально)", "prefix": "ollama_chat", "env": None, "local": True,
     "api_base": "http://localhost:11434", "discover": "ollama"},
    {"id": "lm_studio", "label": "LM Studio (локально)", "prefix": "lm_studio", "env": "LM_STUDIO_API_KEY", "local": True,
     "optional_key": True, "api_base": "http://localhost:1234/v1", "discover": "openai"},
    {"id": "litellm_proxy", "label": "LiteLLM Proxy", "prefix": "litellm_proxy", "env": "LITELLM_PROXY_API_KEY",
     "optional_key": True, "api_base": "http://localhost:4000", "discover": "openai"},
    {"id": "openai_compatible", "label": "OpenAI-совместимый (свой URL)", "prefix": "openai", "env": None,
     "custom_key": True, "api_base": "http://localhost:8000/v1", "discover": "openai"},
]
KEY_ENVS = [p["env"] for p in PROVIDERS if p["env"]]


def _mask(v: str) -> str:
    return "••••" + v[-4:] if len(v) > 8 else "••••"


def key_status(env: str, secrets: dict[str, str]) -> dict[str, Any]:
    if env in secrets:
        return {"env": env, "set": True, "source": "file", "masked": _mask(secrets[env])}
    if os.environ.get(env):
        return {"env": env, "set": True, "source": "env", "masked": _mask(os.environ[env])}
    return {"env": env, "set": False, "source": None, "masked": None}


class TestRequest(BaseModel):
    spec: ModelSpec
    api_key: str | None = None  # an unsaved key typed into the form; used for this probe only


class DiscoverRequest(BaseModel):
    provider: str
    api_base: str
    api_key_env: str | None = None
    api_key: str | None = None


class KeysUpdate(BaseModel):
    values: dict[str, str | None] = Field(default_factory=dict)  # null or "" removes the key


def router(config: Config) -> APIRouter:
    r = APIRouter(prefix="/api/settings")

    def snapshot() -> dict[str, Any]:
        secrets = read_secrets()
        custom = sorted({m.api_key_env for m in config.models if m.api_key_env and m.api_key_env not in KEY_ENVS})
        return {
            "config_path": str(config_path()),
            "config_exists": config_path().exists(),
            "secrets_path": str(SECRETS_FILE),
            "models": [m.model_dump() | {"local": m.is_local} for m in config.models],
            "defaults": config.defaults.model_dump(),
            "providers": PROVIDERS,
            "keys": [key_status(e, secrets) for e in KEY_ENVS + custom],
        }

    @r.get("")
    def get_settings() -> dict[str, Any]:
        return snapshot()

    @r.put("/models")
    def put_models(models: list[ModelSpec]) -> dict[str, Any]:
        ids = [m.id for m in models]
        if len(set(ids)) != len(ids):
            raise HTTPException(400, "model ids must be unique")
        for m in models:
            if "/" not in m.model:
                raise HTTPException(400, f"{m.id}: model must look like provider/name (LiteLLM format)")
            if m.api_key_env and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", m.api_key_env):
                raise HTTPException(400, f"{m.id}: bad environment variable name")
        config.models = models
        d = config.defaults  # drop references to models that no longer exist
        d.council.pinned = [p for p in d.council.pinned if p in ids]
        if d.judge != "auto" and d.judge not in ids:
            d.judge = "auto"
        save_config(config)
        return snapshot()

    @r.put("/defaults")
    def put_defaults(defaults: Defaults) -> dict[str, Any]:
        ids = {m.id for m in config.models}
        bad = [p for p in defaults.council.pinned if p not in ids] + (
            [defaults.judge] if defaults.judge != "auto" and defaults.judge not in ids else [])
        if bad:
            raise HTTPException(400, f"unknown models: {', '.join(bad)}")
        if not (1 <= defaults.council.size <= 9) or defaults.budget_usd <= 0 or defaults.timeout_s <= 0:
            raise HTTPException(400, "council size 1–9, budget and timeout must be positive")
        config.defaults = defaults
        save_config(config)
        return snapshot()

    @r.put("/keys")
    def put_keys(body: KeysUpdate) -> dict[str, Any]:
        secrets = read_secrets()
        for env, value in body.values.items():
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", env):
                raise HTTPException(400, f"bad environment variable name: {env}")
            value = (value or "").strip()
            if value:
                secrets[env] = value
                os.environ[env] = value
            else:
                secrets.pop(env, None)
                os.environ.pop(env, None)
        write_secrets(secrets)
        return snapshot()

    @r.post("/test")
    async def test_model(body: TestRequest) -> dict[str, Any]:
        spec = body.spec
        if body.api_key:  # probe with an unsaved key without touching the environment
            tmp = f"OBJECTION_PROBE_{os.getpid()}"
            os.environ[tmp] = body.api_key
            spec = spec.model_copy(update={"api_key_env": tmp})
        try:
            ok, detail, lat, c = await probe(spec, timeout_s=min(spec.timeout_s or 45, 90))
        finally:
            if body.api_key:
                os.environ.pop(tmp, None)
        return {"ok": ok, "detail": detail, "latency_s": round(lat, 2),
                "reply": (c.text[:200] if c else None), "cost_usd": (c.usage.cost_usd if c else 0.0)}

    @r.get("/catalog")
    def catalog(provider: str) -> list[dict[str, Any]]:
        """Known chat models for a provider from LiteLLM's bundled price list (no network)."""
        import litellm

        out = []
        for name, info in litellm.model_cost.items():
            if info.get("litellm_provider") != provider or info.get("mode") not in ("chat", None):
                continue
            short = name.split("/", 1)[1] if name.startswith(provider + "/") else name
            per_m = lambda k: round(info[k] * 1e6, 3) if info.get(k) else None  # noqa: E731
            out.append({"name": short, "input_per_mtok": per_m("input_cost_per_token"),
                        "output_per_mtok": per_m("output_cost_per_token"),
                        "context": info.get("max_input_tokens")})
        return sorted(out, key=lambda x: x["name"])[:500]

    @r.post("/discover")
    async def discover(body: DiscoverRequest) -> list[str]:
        """List models served by a local server / proxy (Ollama, LM Studio, LiteLLM Proxy, OpenAI-compatible)."""
        preset = next((p for p in PROVIDERS if p["id"] == body.provider), None)
        if not preset or not preset.get("discover"):
            raise HTTPException(400, "this provider has no model discovery")
        base = body.api_base.rstrip("/")
        key = body.api_key or (os.environ.get(body.api_key_env) if body.api_key_env else None) or (
            os.environ.get(preset["env"]) if preset.get("env") else None)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            async with httpx.AsyncClient(timeout=6) as client:
                if preset["discover"] == "ollama":
                    resp = await client.get(f"{base}/api/tags")
                    resp.raise_for_status()
                    return sorted(m["name"] for m in resp.json().get("models", []))
                urls = [f"{base}/models"] + ([] if base.endswith("/v1") else [f"{base}/v1/models"])
                last: Exception | None = None
                for url in urls:
                    try:
                        resp = await client.get(url, headers=headers)
                        resp.raise_for_status()
                        return sorted(m["id"] for m in resp.json().get("data", []))
                    except Exception as exc:  # noqa: BLE001
                        last = exc
                raise last or RuntimeError("no response")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"не удалось получить список моделей с {base}: {type(exc).__name__}: {exc}") from exc

    return r
