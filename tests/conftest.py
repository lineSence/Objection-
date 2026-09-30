import pytest

from objection.config import Config, Defaults, ModelSpec
from objection.store import RunStore


@pytest.fixture
def config() -> Config:
    return Config(
        models=[ModelSpec(id=i, model=f"mock/{i}") for i in ("a", "b", "c")] + [ModelSpec(id="bad", model="mock/fail")],
        defaults=Defaults(),
    )


@pytest.fixture
def store(tmp_path) -> RunStore:
    return RunStore(tmp_path / "runs.sqlite")
