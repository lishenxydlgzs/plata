"""PipelineStore: SQLite-backed persistence for jobs, progress, and run history."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from .types import ProgressStatus, RunLog, RunRecord, StepProgress

PIPELINE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_jobs (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  cadence    TEXT NOT NULL,
  connector_id TEXT NOT NULL,
  last_run_at TEXT,
  registered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
  id              TEXT PRIMARY KEY,
  job_id          TEXT NOT NULL,
  started_at      TEXT NOT NULL,
  completed_at    TEXT,
  status          TEXT NOT NULL DEFAULT 'running',
  units_processed INTEGER NOT NULL DEFAULT 0,
  units_failed    INTEGER NOT NULL DEFAULT 0,
  error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_job ON pipeline_runs(job_id, started_at DESC);

CREATE TABLE IF NOT EXISTS pipeline_progress (
  unit_source_type TEXT NOT NULL,
  unit_source_id   TEXT NOT NULL,
  job_id           TEXT NOT NULL,
  step_id          TEXT NOT NULL,
  run_id           TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'pending',
  error            TEXT,
  completed_at     TEXT,
  PRIMARY KEY (job_id, step_id, unit_source_type, unit_source_id)
);
CREATE INDEX IF NOT EXISTS idx_pipeline_progress_run ON pipeline_progress(run_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_progress_status ON pipeline_progress(job_id, step_id, status);

CREATE TABLE IF NOT EXISTS pipeline_run_logs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     TEXT NOT NULL,
  step_id    TEXT NOT NULL,
  log_path   TEXT NOT NULL,
  label      TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pipeline_run_logs_run ON pipeline_run_logs(run_id);
"""


def initialize_pipeline_db(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(PIPELINE_SCHEMA_SQL)


class PipelineStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._db.row_factory = sqlite3.Row

    # ─── Job Registration ────────────────────────────────────────────────────

    def register_job(self, id: str, name: str, cadence: str, connector_id: str) -> None:
        now = _now()
        self._db.execute(
            """
            INSERT INTO pipeline_jobs (id, name, cadence, connector_id, registered_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name = excluded.name, cadence = excluded.cadence, connector_id = excluded.connector_id
            """,
            (id, name, cadence, connector_id, now),
        )
        self._db.commit()

    def unregister_job(self, id: str) -> bool:
        cursor = self._db.execute("DELETE FROM pipeline_jobs WHERE id = ?", (id,))
        self._db.commit()
        return cursor.rowcount > 0

    def get_job(self, id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT id, name, cadence, connector_id, last_run_at, registered_at FROM pipeline_jobs WHERE id = ?",
            (id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "cadence": row["cadence"],
            "connector_id": row["connector_id"],
            "last_run_at": row["last_run_at"],
            "registered_at": row["registered_at"],
        }

    def list_jobs(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT id, name, cadence, connector_id, last_run_at, registered_at FROM pipeline_jobs ORDER BY registered_at"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "cadence": r["cadence"],
                "connector_id": r["connector_id"],
                "last_run_at": r["last_run_at"],
                "registered_at": r["registered_at"],
            }
            for r in rows
        ]

    def update_job_last_run(self, job_id: str, at: str) -> None:
        self._db.execute("UPDATE pipeline_jobs SET last_run_at = ? WHERE id = ?", (at, job_id))
        self._db.commit()

    # ─── Run Records ─────────────────────────────────────────────────────────

    def create_run(self, id: str, job_id: str) -> RunRecord:
        now = _now()
        self._db.execute(
            "INSERT INTO pipeline_runs (id, job_id, started_at, status, units_processed, units_failed) VALUES (?, ?, ?, 'running', 0, 0)",
            (id, job_id, now),
        )
        self._db.commit()
        return RunRecord(id=id, job_id=job_id, started_at=now, status="running", units_processed=0, units_failed=0)

    def complete_run(self, id: str, status: str, units_processed: int, units_failed: int, error: str | None = None) -> None:
        now = _now()
        self._db.execute(
            "UPDATE pipeline_runs SET completed_at = ?, status = ?, units_processed = ?, units_failed = ?, error = ? WHERE id = ?",
            (now, status, units_processed, units_failed, error, id),
        )
        self._db.commit()

    def get_run(self, id: str) -> RunRecord | None:
        row = self._db.execute(
            "SELECT id, job_id, started_at, completed_at, status, units_processed, units_failed, error FROM pipeline_runs WHERE id = ?",
            (id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_run(row)

    def get_runs_for_job(self, job_id: str, limit: int = 20) -> list[RunRecord]:
        rows = self._db.execute(
            "SELECT id, job_id, started_at, completed_at, status, units_processed, units_failed, error FROM pipeline_runs WHERE job_id = ? ORDER BY started_at DESC, rowid DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def get_recent_runs(self, limit: int = 20) -> list[RunRecord]:
        rows = self._db.execute(
            "SELECT id, job_id, started_at, completed_at, status, units_processed, units_failed, error FROM pipeline_runs ORDER BY started_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def get_last_run_for_job(self, job_id: str) -> RunRecord | None:
        runs = self.get_runs_for_job(job_id, limit=1)
        return runs[0] if runs else None

    # ─── Step Progress ────────────────────────────────────────────────────────

    def record_progress(self, progress: StepProgress) -> None:
        self._db.execute(
            """
            INSERT INTO pipeline_progress (unit_source_type, unit_source_id, job_id, step_id, run_id, status, error, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id, step_id, unit_source_type, unit_source_id) DO UPDATE SET
              run_id = excluded.run_id, status = excluded.status, error = excluded.error, completed_at = excluded.completed_at
            """,
            (
                progress.unit_source_type,
                progress.unit_source_id,
                progress.job_id,
                progress.step_id,
                progress.run_id,
                progress.status,
                progress.error,
                progress.completed_at,
            ),
        )
        self._db.commit()

    def get_progress(self, job_id: str, step_id: str, unit_source_type: str, unit_source_id: str) -> StepProgress | None:
        row = self._db.execute(
            """
            SELECT unit_source_type, unit_source_id, job_id, step_id, run_id, status, error, completed_at
            FROM pipeline_progress
            WHERE job_id = ? AND step_id = ? AND unit_source_type = ? AND unit_source_id = ?
            """,
            (job_id, step_id, unit_source_type, unit_source_id),
        ).fetchone()
        if not row:
            return None
        return StepProgress(
            unit_source_type=row["unit_source_type"],
            unit_source_id=row["unit_source_id"],
            job_id=row["job_id"],
            step_id=row["step_id"],
            run_id=row["run_id"],
            status=row["status"],
            error=row["error"],
            completed_at=row["completed_at"],
        )

    def get_unprocessed(self, job_id: str, step_id: str, unit_source_type: str, unit_source_ids: list[str]) -> list[str]:
        if not unit_source_ids:
            return []
        completed: set[str] = set()
        batch_size = 500
        for i in range(0, len(unit_source_ids), batch_size):
            batch = unit_source_ids[i : i + batch_size]
            placeholders = ",".join("?" * len(batch))
            rows = self._db.execute(
                f"""
                SELECT unit_source_id FROM pipeline_progress
                WHERE job_id = ? AND step_id = ? AND unit_source_type = ? AND unit_source_id IN ({placeholders})
                AND status = 'completed'
                """,
                (job_id, step_id, unit_source_type, *batch),
            ).fetchall()
            for row in rows:
                completed.add(row["unit_source_id"])
        return [id for id in unit_source_ids if id not in completed]

    def get_progress_for_run(self, run_id: str) -> list[StepProgress]:
        rows = self._db.execute(
            "SELECT unit_source_type, unit_source_id, job_id, step_id, run_id, status, error, completed_at FROM pipeline_progress WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        return [
            StepProgress(
                unit_source_type=r["unit_source_type"],
                unit_source_id=r["unit_source_id"],
                job_id=r["job_id"],
                step_id=r["step_id"],
                run_id=r["run_id"],
                status=r["status"],
                error=r["error"],
                completed_at=r["completed_at"],
            )
            for r in rows
        ]

    # ─── Run Logs ────────────────────────────────────────────────────────────

    def add_run_log(self, run_id: str, step_id: str, log_path: str, label: str | None = None) -> None:
        now = _now()
        self._db.execute(
            "INSERT INTO pipeline_run_logs (run_id, step_id, log_path, label, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, step_id, log_path, label, now),
        )
        self._db.commit()

    def get_run_logs(self, run_id: str) -> list[RunLog]:
        rows = self._db.execute(
            "SELECT id, run_id, step_id, log_path, label, created_at FROM pipeline_run_logs WHERE run_id = ? ORDER BY created_at ASC",
            (run_id,),
        ).fetchall()
        return [
            RunLog(id=r["id"], run_id=r["run_id"], step_id=r["step_id"], log_path=r["log_path"], label=r["label"], created_at=r["created_at"])
            for r in rows
        ]

    # ─── Private ─────────────────────────────────────────────────────────────

    def _row_to_run(self, row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=row["id"],
            job_id=row["job_id"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            status=row["status"],
            units_processed=row["units_processed"],
            units_failed=row["units_failed"],
            error=row["error"],
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
