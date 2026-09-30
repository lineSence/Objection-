"""Run Store: a complete, reproducible log of every run in SQLite."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

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
CREATE TABLE IF NOT EXISTS cache (
  key TEXT PRIMARY KEY,
  run_id TEXT NOT NULL
);
-- M3.1: one row per (run, council member) — the raw material of the Model Registry and the eval "mine" suite.
CREATE TABLE IF NOT EXISTS model_outcomes (
  run_id TEXT NOT NULL,
  model_id TEXT NOT NULL,
  model TEXT,
  family TEXT,
  mode TEXT NOT NULL,
  source TEXT NOT NULL,
  created_at TEXT NOT NULL,
  answer_key TEXT,
  final_key TEXT,
  changed INTEGER,
  agreed INTEGER,
  correct INTEGER,
  claims_supported INTEGER NOT NULL DEFAULT 0,
  claims_refuted INTEGER NOT NULL DEFAULT 0,
  tests_passed INTEGER,
  findings_reported INTEGER NOT NULL DEFAULT 0,
  findings_confirmed INTEGER NOT NULL DEFAULT 0,
  findings_rejected INTEGER NOT NULL DEFAULT 0,
  calls INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  latency_s REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, model_id)
);
CREATE INDEX IF NOT EXISTS outcomes_model ON model_outcomes(model_id, mode);
-- M4: eval reports (the runs themselves are in `runs` with source = 'eval').
CREATE TABLE IF NOT EXISTS evals (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  suite TEXT NOT NULL,
  pool TEXT NOT NULL,
  status TEXT NOT NULL,
  body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evals_pool ON evals(pool, created_at DESC);
"""

OUTCOME_COLUMNS = ("run_id", "model_id", "model", "family", "mode", "source", "created_at", "answer_key", "final_key",
                   "changed", "agreed", "correct", "claims_supported", "claims_refuted", "tests_passed",
                   "findings_reported", "findings_confirmed", "findings_rejected", "calls", "errors", "cost_usd",
                   "latency_s")


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

    def list_runs(self, limit: int = 50, include_eval: bool = True) -> list[Run]:
        where = "" if include_eval else "WHERE source != 'eval' "
        with self._lock:
            rows = self._db.execute(f"SELECT body FROM runs {where}ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
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

    def delete_run(self, run_id: str) -> bool:
        """Delete a run with its events, cache entries and outcomes. Returns False if it did not exist."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM events WHERE run_id=?", (run_id,))
            self._db.execute("DELETE FROM model_outcomes WHERE run_id=?", (run_id,))
            self._db.execute("DELETE FROM cache WHERE run_id=?", (run_id,))
            return self._db.execute("DELETE FROM runs WHERE id=?", (run_id,)).rowcount > 0

    def put_cache(self, key: str, run_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO cache(key, run_id) VALUES (?,?)", (key, run_id))

    def get_cache(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT run_id FROM cache WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def list_all_runs(self, *, labeled_only: bool = False) -> list[Run]:
        with self._lock:
            rows = self._db.execute("SELECT body FROM runs ORDER BY created_at").fetchall()
        runs = [Run.model_validate_json(r[0]) for r in rows]
        return [r for r in runs if r.label or r.expected] if labeled_only else runs

    # ---------- model outcomes (M3.1) ----------

    def put_outcomes(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        """Replace the outcome rows of one run (idempotent: labels and re-indexing rewrite them)."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM model_outcomes WHERE run_id=?", (run_id,))
            self._db.executemany(
                f"INSERT INTO model_outcomes({','.join(OUTCOME_COLUMNS)}) VALUES ({','.join('?' * len(OUTCOME_COLUMNS))})",
                [tuple(r.get(c) for c in OUTCOME_COLUMNS) for r in rows])

    def outcomes(self, *, model_id: str | None = None, mode: str | None = None, run_id: str | None = None,
                 include_eval: bool = True) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM model_outcomes WHERE 1=1", []
        for col, val in (("model_id", model_id), ("mode", mode), ("run_id", run_id)):
            if val is not None:
                q += f" AND {col}=?"
                args.append(val)
        if not include_eval:
            q += " AND source != 'eval'"
        with self._lock:
            cur = self._db.execute(q + " ORDER BY created_at", args)
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]

    # ---------- evals (M4) ----------

    def save_eval(self, eval_id: str, created_at: str, suite: str, pool: str, status: str, body: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO evals(id, created_at, suite, pool, status, body) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, body=excluded.body",
                (eval_id, created_at, suite, pool, status, body))

    def list_evals(self, limit: int = 50, pool: str | None = None) -> list[str]:
        q, args = "SELECT body FROM evals", []
        if pool:
            q += " WHERE pool=?"
            args.append(pool)
        with self._lock:
            return [r[0] for r in self._db.execute(q + " ORDER BY created_at DESC LIMIT ?", (*args, limit)).fetchall()]

    def get_eval(self, eval_id: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM evals WHERE id=?", (eval_id,)).fetchone()
        return row[0] if row else None

    def delete_eval(self, eval_id: str) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM evals WHERE id=?", (eval_id,)).rowcount > 0
