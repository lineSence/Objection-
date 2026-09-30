import json
import time

from fastapi.testclient import TestClient

from objection.server import create_app


def test_api_run_and_stream(config, store):
    with TestClient(create_app(config, store)) as client:
        _scenario(client)


def _scenario(client):
    assert client.get("/api/health").json()["ok"]
    assert [m["id"] for m in client.get("/api/models").json()] == ["a", "b", "c", "bad"]

    run = client.post("/api/runs", json={"question": "Что выбрать?"}).json()
    assert run["status"] == "queued"

    with client.stream("GET", f"/api/runs/{run['id']}/stream") as resp:
        events = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]
    assert events[0]["type"] == "run_started"
    assert events[-1]["type"] == "run_finished"

    for _ in range(50):
        body = client.get(f"/api/runs/{run['id']}").json()
        if body["status"] == "done":
            break
        time.sleep(0.05)
    assert body["verdict"]["answer"]
    assert client.get("/api/runs").json()[0]["id"] == run["id"]


def test_unknown_model_is_400(config, store):
    client = TestClient(create_app(config, store))
    assert client.post("/api/runs", json={"question": "q", "models": ["nope"]}).status_code == 400
