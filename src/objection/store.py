"""Run Store: a complete, reproducible log of every run in SQLite."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .schemas import Event, Run

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,
  source TEXT NOT NULL,
  body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_created ON runs(created_at DESC);
CREATE TABLE IF NOT EXISTS events (
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  body TEXT NOT NULL,
  PRIMARY KEY (run_id, seq)
);
"""


class RunStore:
    def __init__(self, path: Path | str):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def save_run(self, run: Run) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO runs(id, created_at, status, source, body) VALUES (?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, body=excluded.body",
                (run.id, run.created_at.isoformat(), run.status, run.source, run.model_dump_json()),
            )

    def get_run(self, run_id: str) -> Run | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
        return Run.model_validate_json(row[0]) if row else None

    def list_runs(self, limit: int = 50) -> list[Run]:
        with self._lock:
            rows = self._db.execute("SELECT body FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [Run.model_validate_json(r[0]) for r in rows]

    def add_event(self, event: Event) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO events(run_id, seq, body) VALUES (?,?,?)",
                (event.run_id, event.seq, event.model_dump_json()),
            )

    def events(self, run_id: str, after: int = 0) -> list[Event]:
        with self._lock:
            rows = self._db.execute(
                "SELECT body FROM events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after)
            ).fetchall()
        return [Event.model_validate_json(r[0]) for r in rows]
