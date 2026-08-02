"""Scheduler: manages the execution loop, checking which jobs are due and running them."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from .engine import EngineOptions, PipelineEngine
from .store import PipelineStore
from .types import JobDefinition, RunResult


def parse_cadence(cadence: str) -> int:
    """Parse a cadence string like '30m', '1h', '5s', '1d' into milliseconds."""
    pattern = re.compile(r"^(\d+)(s|m|h|d)$")
    match = pattern.match(cadence.strip())
    if not match:
        try:
            ms = int(cadence)
            if ms > 0:
                return ms
        except ValueError:
            pass
        raise ValueError(f'Invalid cadence format: "{cadence}". Use formats like "30m", "1h", "5s", or "1d".')
    value = int(match.group(1))
    unit = match.group(2)
    multipliers = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
    return value * multipliers[unit]


@dataclass
class SchedulerOptions:
    store: PipelineStore
    tick_interval_ms: int = 60_000
    on_log: Callable[[str, str, str, str], None] | None = None
    on_run_complete: Callable[[RunResult], None] | None = None


class Scheduler:
    def __init__(self, opts: SchedulerOptions) -> None:
        self._store = opts.store
        self._tick_interval_ms = opts.tick_interval_ms
        self._on_run_complete = opts.on_run_complete
        self._engine = PipelineEngine(EngineOptions(store=opts.store, on_log=opts.on_log))
        self._jobs: dict[str, JobDefinition] = {}
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def register(self, job: JobDefinition) -> None:
        self._jobs[job.id] = job
        self._store.register_job(job.id, job.name, job.cadence, job.connector.id)

    def unregister(self, job_id: str) -> bool:
        removed = job_id in self._jobs
        self._jobs.pop(job_id, None)
        if removed:
            self._store.unregister_job(job_id)
        return removed

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.ensure_future(self._loop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def run_now(self, job_id: str) -> RunResult:
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(f"Job not found: {job_id}")
        last_run = self._store.get_last_run_for_job(job_id)
        since = datetime.fromisoformat(last_run.started_at) if last_run else None
        result = await self._engine.run(job, since)
        if self._on_run_complete:
            self._on_run_complete(result)
        return result

    def get_jobs_due(self) -> list[JobDefinition]:
        now_ms = _now_ms()
        due: list[JobDefinition] = []
        for job in self._jobs.values():
            interval_ms = parse_cadence(job.cadence)
            job_record = self._store.get_job(job.id)
            if not job_record or not job_record["last_run_at"]:
                due.append(job)
                continue
            last_run_time = datetime.fromisoformat(job_record["last_run_at"]).timestamp() * 1000
            if now_ms - last_run_time >= interval_ms:
                due.append(job)
        return due

    async def tick(self) -> None:
        due_jobs = self.get_jobs_due()
        for job in due_jobs:
            try:
                result = await self._engine.run(job, self._get_last_run_date(job.id))
                if self._on_run_complete:
                    self._on_run_complete(result)
            except Exception:
                pass

    def get_registered_jobs(self) -> list[JobDefinition]:
        return list(self._jobs.values())

    @property
    def is_running(self) -> bool:
        return self._running

    async def _loop(self) -> None:
        while self._running:
            await self.tick()
            await asyncio.sleep(self._tick_interval_ms / 1000)

    def _get_last_run_date(self, job_id: str) -> datetime | None:
        job_record = self._store.get_job(job_id)
        if job_record and job_record["last_run_at"]:
            return datetime.fromisoformat(job_record["last_run_at"])
        return None


def create_scheduler(store: PipelineStore, **kwargs: Any) -> Scheduler:
    return Scheduler(SchedulerOptions(store=store, **kwargs))


def _now_ms() -> float:
    return datetime.now().timestamp() * 1000
