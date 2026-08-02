"""Pipeline types for the map/reduce/filter step-based architecture."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal, Protocol


# ─── Work Units & Connectors ─────────────────────────────────────────────────


@dataclass
class WorkUnit:
    source_type: str
    source_id: str
    payload: Any
    timestamp: datetime


class Connector(Protocol):
    id: str
    source_type: str

    async def poll(self, since: datetime | None) -> list[WorkUnit]: ...


# ─── Step Types ──────────────────────────────────────────────────────────────


@dataclass
class StepContext:
    job_id: str
    run_id: str
    step_id: str
    _log_fn: Callable[[str], None] | None = None

    def log(self, message: str) -> None:
        if self._log_fn:
            self._log_fn(message)


@dataclass
class MapStep:
    id: str
    name: str
    handler: Callable[[Any, StepContext], Awaitable[Any]]
    type: Literal["map"] = "map"


@dataclass
class ReduceStep:
    id: str
    name: str
    handler: Callable[[list[Any], StepContext], Awaitable[Any]]
    type: Literal["reduce"] = "reduce"


@dataclass
class FilterStep:
    id: str
    name: str
    handler: Callable[[Any, StepContext], Awaitable[bool]]
    type: Literal["filter"] = "filter"


Step = MapStep | ReduceStep | FilterStep


# ─── Job Definition ──────────────────────────────────────────────────────────


@dataclass
class JobDefinition:
    id: str
    name: str
    cadence: str
    connector: Connector
    steps: list[Step]


# ─── Progress Tracking ───────────────────────────────────────────────────────

ProgressStatus = Literal["pending", "completed", "failed", "skipped"]


@dataclass
class StepProgress:
    unit_source_type: str
    unit_source_id: str
    job_id: str
    step_id: str
    run_id: str
    status: ProgressStatus
    error: str | None = None
    completed_at: str | None = None


@dataclass
class RunRecord:
    id: str
    job_id: str
    started_at: str
    status: Literal["running", "completed", "failed"]
    units_processed: int
    units_failed: int
    completed_at: str | None = None
    error: str | None = None


# ─── Run Logs ────────────────────────────────────────────────────────────────


@dataclass
class RunLog:
    id: int
    run_id: str
    step_id: str
    log_path: str
    created_at: str
    label: str | None = None


# ─── Engine Result ───────────────────────────────────────────────────────────


@dataclass
class RunResult:
    run_id: str
    job_id: str
    status: Literal["completed", "failed"]
    units_processed: int
    units_failed: int
    units_skipped: int
    started_at: str
    completed_at: str
    error: str | None = None
