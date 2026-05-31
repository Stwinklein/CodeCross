from __future__ import annotations

"""SQLite persistence for CodeCross runs.

V1.5 intentionally stores each RunRecord as JSON plus a few indexed columns.
That keeps the database durable without locking the project into a premature
table design while the workflow schema is still evolving.
"""

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path(".codecross") / "runs.sqlite3"


class SQLiteRunStore:
    """Small repository object around the standard-library sqlite3 module."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._ensure_schema()

    @classmethod
    def from_environment(cls) -> "SQLiteRunStore":
        """Build the store from CODECROSS_RUN_DB or the local default path."""

        return cls(os.getenv("CODECROSS_RUN_DB", str(DEFAULT_DB_PATH)))

    def _connect(self) -> sqlite3.Connection:
        """Open a short-lived connection.

        Short-lived connections avoid sharing sqlite connection objects across
        FastAPI request threads and background workflow threads.
        """

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        """Create the V1.5 schema if this is the first run on the machine."""

        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    record_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_updated_at ON runs(updated_at DESC)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)")

    def upsert_run(self, record: dict[str, Any]) -> None:
        """Insert or replace one full RunRecord JSON document."""

        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (run_id, status, created_at, updated_at, finished_at, record_json)
                VALUES (:run_id, :status, :created_at, :updated_at, :finished_at, :record_json)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    finished_at = excluded.finished_at,
                    record_json = excluded.record_json
                """,
                {
                    "run_id": record["run_id"],
                    "status": record["status"],
                    "created_at": record["created_at"],
                    "updated_at": record["updated_at"],
                    "finished_at": record.get("finished_at"),
                    "record_json": json.dumps(record, ensure_ascii=False),
                },
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Load a full RunRecord JSON document by ID."""

        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT record_json FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["record_json"])

    def list_runs(self, *, limit: int = 20, status: str | None = None) -> list[dict[str, Any]]:
        """Return recent RunRecord JSON documents for the history page."""

        bounded_limit = max(1, min(limit, 100))
        with self._lock, self._connect() as connection:
            if status:
                rows = connection.execute(
                    """
                    SELECT record_json FROM runs
                    WHERE status = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (status, bounded_limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT record_json FROM runs
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (bounded_limit,),
                ).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    def clear(self) -> None:
        """Delete all runs, used by tests to keep the suite deterministic."""

        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM runs")
