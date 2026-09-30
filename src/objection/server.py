"""FastAPI app: JSON API + SSE for live runs + the bundled React Web UI (D-009, D-011)."""

from __future__ import annotations

import asyncio
import json
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .bus import EventBus
from .config import Config, load_config
from .engine import Engine
from .providers import health_check
from .schemas import Event, Run, RunRequest
from .store import RunStore

TERMINAL = {"run_finished", "run_failed"}
WEB_DIST = Path(str(files("objection") / "web_dist"))


def create_app(config: Config | None = None, store: RunStore | None = None) -> FastAPI:
    config = config or load_config()
    store = store or RunStore(config.storage.resolved)
    engine = Engine(config, store, EventBus())
    app = FastAPI(title="Objection!", version=__version__)
    app.state.engine = engine
    tasks: set[asyncio.Task] = set()

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/api/models")
    def models() -> list[dict]:
        return [
            {"id": m.id, "model": m.model, "local": m.is_local, "enabled": m.enabled}
            for m in config.models
        ]

    @app.post("/api/models/check")
    async def models_check() -> list[dict]:
        results = await asyncio.gather(*(health_check(m) for m in config.enabled_models))
        return [
            {"id": m.id, "ok": ok, "detail": detail, "latency_s": round(lat, 2)}
            for m, (ok, detail, lat) in zip(config.enabled_models, results)
        ]

    @app.get("/api/runs")
    def list_runs(limit: int = 50) -> list[Run]:
        return store.list_runs(limit)

    @app.post("/api/runs", status_code=201)
    async def create_run(req: RunRequest) -> Run:
        try:
            run = engine.create_run(req)
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        task = asyncio.create_task(engine.execute(run))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return run

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> Run:
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        return run

    @app.get("/api/runs/{run_id}/events")
    def get_events(run_id: str, after: int = 0) -> list[Event]:
        return store.events(run_id, after)

    @app.get("/api/runs/{run_id}/stream")
    async def stream(run_id: str, after: int = 0) -> StreamingResponse:
        if not store.get_run(run_id):
            raise HTTPException(404, "run not found")

        async def gen():
            q = engine.bus.subscribe(run_id)
            try:
                last = after
                for ev in store.events(run_id, after):
                    last = ev.seq
                    yield _sse(ev)
                    if ev.type in TERMINAL:
                        return
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if ev.seq <= last:
                        continue
                    last = ev.seq
                    yield _sse(ev)
                    if ev.type in TERMINAL:
                        return
            finally:
                engine.bus.unsubscribe(run_id, q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    if (WEB_DIST / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            candidate = WEB_DIST / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(WEB_DIST / "index.html")

    return app


def _sse(ev: Event) -> str:
    return f"id: {ev.seq}\nevent: {ev.type}\ndata: {json.dumps(ev.model_dump(mode='json'), ensure_ascii=False)}\n\n"
