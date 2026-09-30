"""User configuration: the model pool is defined by the user and may change at any time (D-006)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

DEFAULT_HOME = Path(os.environ.get("OBJECTION_HOME", "~/.objection")).expanduser()


class ModelSpec(BaseModel):
    id: str = Field(pattern=r"^[\w.-]{1,40}$")
    model: str = Field(min_length=3)  # LiteLLM model string "provider/model", e.g. "openai/<name>" or "mock/a"
    api_base: str | None = None
    api_key_env: str | None = None  # env var with this model's key; default: the provider's standard variable
    timeout_s: float | None = None
    max_parallel: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)  # passed to litellm.completion (temperature, max_tokens, …)
    enabled: bool = True

    @property
    def is_local(self) -> bool:
        return self.model.split("/", 1)[0] in {"ollama", "ollama_chat", "lm_studio", "hosted_vllm"} or (
            self.api_base is not None and ("localhost" in self.api_base or "127.0.0.1" in self.api_base)
        )


class CouncilDefaults(BaseModel):
    size: int = 3
    selection: str = "auto"
    pinned: list[str] = Field(default_factory=list)


class Defaults(BaseModel):
    mode: str = "deliberate"
    council: CouncilDefaults = Field(default_factory=CouncilDefaults)
    judge: str = "auto"
    budget_usd: float = 0.50
    timeout_s: float = 120
    anonymize: bool = True
    max_critique_rounds: int = 1


class Storage(BaseModel):
    path: str = str(DEFAULT_HOME / "runs.sqlite")

    @property
    def resolved(self) -> Path:
        return Path(self.path).expanduser()


class Config(BaseModel):
    models: list[ModelSpec] = Field(default_factory=list)
    defaults: Defaults = Field(default_factory=Defaults)
    storage: Storage = Field(default_factory=Storage)

    def model(self, model_id: str) -> ModelSpec:
        for m in self.models:
            if m.id == model_id:
                return m
        raise KeyError(f"model '{model_id}' is not in the pool")

    @property
    def enabled_models(self) -> list[ModelSpec]:
        return [m for m in self.models if m.enabled]


def config_path() -> Path:
    env = os.environ.get("OBJECTION_CONFIG")
    if env:
        return Path(env).expanduser()
    local = Path("objection.yaml")
    if local.exists():
        return local
    return DEFAULT_HOME / "config.yaml"


SECRETS_FILE = DEFAULT_HOME / "secrets.env"


def read_secrets() -> dict[str, str]:
    """API keys saved from the Web UI. Kept outside the config (never in a repo), file mode 0600."""
    if not SECRETS_FILE.exists():
        return {}
    out: dict[str, str] = {}
    for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def write_secrets(values: dict[str, str]) -> None:
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    body = "# Objection! API keys (saved from the Web UI). Do not commit.\n" + "".join(
        f"{k}={v}\n" for k, v in sorted(values.items()))
    fd = os.open(SECRETS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    try:
        os.chmod(SECRETS_FILE, 0o600)
    except OSError:
        pass


def load_secrets() -> None:
    """Keys saved in the Web UI take precedence over the shell environment (the UI is the explicit choice)."""
    os.environ.update(read_secrets())


MOCK_POOL = [ModelSpec(id="mock-a", model="mock/a"), ModelSpec(id="mock-b", model="mock/b"),
             ModelSpec(id="mock-c", model="mock/c")]


def load_config(path: Path | None = None) -> Config:
    load_secrets()
    path = path or config_path()
    if not path.exists():
        # No config yet: a mock-only pool so that everything works out of the box.
        return Config(models=[m.model_copy() for m in MOCK_POOL])
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)


def save_config(config: Config, path: Path | None = None) -> Path:
    """Write models/defaults back to YAML, keeping other sections (e.g. `verifier`) untouched."""
    path = path or config_path()
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        path.with_suffix(path.suffix + ".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    raw["models"] = [m.model_dump(exclude_defaults=True) | {"id": m.id, "model": m.model} for m in config.models]
    raw["defaults"] = config.defaults.model_dump()
    raw.setdefault("storage", {"path": config.storage.path})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Objection! config — edited from the Web UI (Settings) or by hand.\n"
                    + yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path
