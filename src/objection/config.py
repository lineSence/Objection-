"""User configuration: the model pool is defined by the user and may change at any time (D-006)."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_HOME = Path(os.environ.get("OBJECTION_HOME", "~/.objection")).expanduser()


class ModelSpec(BaseModel):
    id: str
    model: str  # LiteLLM model string, e.g. "openai/gpt-4o" or "mock/echo"
    api_base: str | None = None
    timeout_s: float | None = None
    max_parallel: int | None = None
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


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        # No config yet: a mock-only pool so that everything works out of the box.
        return Config(
            models=[
                ModelSpec(id="mock-a", model="mock/a"),
                ModelSpec(id="mock-b", model="mock/b"),
                ModelSpec(id="mock-c", model="mock/c"),
            ]
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)
