import os
import stat

import pytest
import yaml
from fastapi.testclient import TestClient

import objection.config as cfg
import objection.settings as st
from objection.server import create_app


@pytest.fixture
def client(config, store, tmp_path, monkeypatch):
    monkeypatch.setenv("OBJECTION_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setattr(cfg, "SECRETS_FILE", tmp_path / "secrets.env")
    monkeypatch.setattr(st, "SECRETS_FILE", tmp_path / "secrets.env")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app(config, store)) as c:
        yield c
    os.environ.pop("OPENAI_API_KEY", None)


def test_models_saved_to_yaml_and_applied(client, tmp_path):
    s = client.get("/api/settings").json()
    assert any(p["id"] == "litellm_proxy" for p in s["providers"])
    models = [m for m in s["models"] if m["id"] != "bad"] + [
        {"id": "local", "model": "ollama_chat/qwen", "api_base": "http://localhost:11434", "params": {"temperature": 0.2}}]
    s = client.put("/api/settings/models", json=models).json()
    assert [m["id"] for m in s["models"]] == ["a", "b", "c", "local"]
    assert s["models"][-1]["local"] is True
    saved = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert saved["models"][-1] == {"id": "local", "model": "ollama_chat/qwen", "api_base": "http://localhost:11434",
                                   "params": {"temperature": 0.2}}
    assert [m["id"] for m in client.get("/api/models").json()] == ["a", "b", "c", "local"]  # live, no restart
    assert client.put("/api/settings/models", json=models + [models[0]]).status_code == 400  # duplicate id
    assert client.put("/api/settings/models", json=[{"id": "x", "model": "noprefix"}]).status_code == 400


def test_defaults_validation(client):
    d = client.get("/api/settings").json()["defaults"]
    d["council"]["pinned"] = ["a", "b"]
    d["budget_usd"] = 0.2
    assert client.put("/api/settings/defaults", json=d).json()["defaults"]["council"]["pinned"] == ["a", "b"]
    d["judge"] = "nope"
    assert client.put("/api/settings/defaults", json=d).status_code == 400


def test_keys_are_masked_and_private(client, tmp_path):
    s = client.put("/api/settings/keys", json={"values": {"OPENAI_API_KEY": "sk-test-1234567890"}}).json()
    k = next(k for k in s["keys"] if k["env"] == "OPENAI_API_KEY")
    assert k == {"env": "OPENAI_API_KEY", "set": True, "source": "file", "masked": "••••7890"}
    assert "sk-test-1234567890" not in client.get("/api/settings").text
    f = tmp_path / "secrets.env"
    assert "OPENAI_API_KEY=sk-test-1234567890" in f.read_text()
    if os.name == "posix":
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert os.environ["OPENAI_API_KEY"] == "sk-test-1234567890"
    s = client.put("/api/settings/keys", json={"values": {"OPENAI_API_KEY": None}}).json()
    assert not next(k for k in s["keys"] if k["env"] == "OPENAI_API_KEY")["set"]
    assert "OPENAI_API_KEY" not in os.environ


def test_probe_and_catalog(client):
    r = client.post("/api/settings/test", json={"spec": {"id": "t", "model": "mock/a"}}).json()
    assert r["ok"] and r["reply"] == "ok"
    r = client.post("/api/settings/test", json={"spec": {"id": "t", "model": "mock/fail"}}).json()
    assert not r["ok"] and "mock failure" in r["detail"]
    names = [m["name"] for m in client.get("/api/settings/catalog", params={"provider": "anthropic"}).json()]
    assert names and all("/" not in n or n.count("/") >= 1 for n in names)


def test_cross_site_writes_refused(client):
    r = client.put("/api/settings/keys", json={"values": {}}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 403
    assert client.put("/api/settings/keys", json={"values": {}}, headers={"Origin": "http://127.0.0.1:6967"}).status_code == 200
