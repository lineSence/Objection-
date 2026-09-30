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


@pytest.fixture(autouse=True)
def no_real_search(monkeypatch):
    """Tests never hit a real SearXNG (a developer may run one on :8888); tests that need search patch it again."""
    import objection.verifier as V
    from objection.search import SearchError

    async def down(query, cfg, k=None):
        raise SearchError("search disabled in tests")

    monkeypatch.setattr(V, "searxng", down)
