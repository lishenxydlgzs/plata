"""Pipeline Engine: executes a job by polling its connector, then running each step in sequence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .store import PipelineStore
from .types import FilterStep, JobDefinition, MapStep, ReduceStep, RunResult, StepContext, StepProgress


@dataclass
class EngineOptions:
    store: PipelineStore
    on_log: Callable[[str, str, str, str], None] | None = None


class PipelineEngine:
    def __init__(self, opts: EngineOptions) -> None:
        self._store = opts.store
        self._on_log = opts.on_log

    async def run(self, job: JobDefinition, since: datetime | None = None) -> RunResult:
        run_id = str(uuid.uuid4())
        started_at = _now()
        self._store.create_run(run_id, job.id)

        units_processed = 0
        units_failed = 0
        units_skipped = 0
        run_error: str | None = None

        try:
            units = await job.connector.poll(since)

            if not units:
                self._store.complete_run(run_id, "completed", 0, 0)
                self._store.update_job_last_run(job.id, started_at)
                return RunResult(
                    run_id=run_id, job_id=job.id, status="completed",
                    units_processed=0, units_failed=0, units_skipped=0,
                    started_at=started_at, completed_at=_now(),
                )

            current_items: list[Any] = list(units)

            for step in job.steps:
                def log_fn(message: str, _sid=step.id) -> None:
                    if self._on_log:
                        self._on_log(job.id, run_id, _sid, message)

                ctx = StepContext(job_id=job.id, run_id=run_id, step_id=step.id, _log_fn=log_fn)

                if isinstance(step, MapStep):
                    result = await self._run_map_step(step, current_items, ctx, job.id, run_id)
                    current_items = result["outputs"]
                    units_processed += result["processed"]
                    units_failed += result["failed"]
                elif isinstance(step, FilterStep):
                    result = await self._run_filter_step(step, current_items, ctx, job.id, run_id)
                    current_items = result["passed"]
                    units_skipped += result["skipped"]
                    units_processed += len(result["passed"])
                elif isinstance(step, ReduceStep):
                    result = await self._run_reduce_step(step, current_items, ctx, job.id, run_id)
                    if result.get("error"):
                        run_error = result["error"]
                        units_failed += len(current_items)
                        break
                    current_items = [result["output"]] if result.get("output") is not None else []
                    units_processed += 1

            status = "failed" if run_error else "completed"
            self._store.complete_run(run_id, status, units_processed, units_failed, run_error)
            self._store.update_job_last_run(job.id, started_at)

            return RunResult(
                run_id=run_id, job_id=job.id, status=status,
                units_processed=units_processed, units_failed=units_failed, units_skipped=units_skipped,
                started_at=started_at, completed_at=_now(), error=run_error,
            )
        except Exception as err:
            error_msg = str(err)
            self._store.complete_run(run_id, "failed", units_processed, units_failed, error_msg)
            self._store.update_job_last_run(job.id, started_at)
            return RunResult(
                run_id=run_id, job_id=job.id, status="failed",
                units_processed=units_processed, units_failed=units_failed, units_skipped=units_skipped,
                started_at=started_at, completed_at=_now(), error=error_msg,
            )

    async def _run_map_step(
        self, step: MapStep, items: list[Any], ctx: StepContext, job_id: str, run_id: str
    ) -> dict[str, Any]:
        outputs: list[Any] = []
        processed = 0
        failed = 0

        for item in items:
            unit = self._extract_work_unit(item)
            try:
                result = await step.handler(item, ctx)
                outputs.append(result)
                processed += 1
                if unit:
                    self._store.record_progress(StepProgress(
                        unit_source_type=unit["source_type"], unit_source_id=unit["source_id"],
                        job_id=job_id, step_id=step.id, run_id=run_id, status="completed",
                        completed_at=_now(),
                    ))
            except Exception as err:
                failed += 1
                if unit:
                    self._store.record_progress(StepProgress(
                        unit_source_type=unit["source_type"], unit_source_id=unit["source_id"],
                        job_id=job_id, step_id=step.id, run_id=run_id, status="failed",
                        error=str(err), completed_at=_now(),
                    ))
                ctx.log(f'Map step "{step.name}" failed for item: {err}')

        return {"outputs": outputs, "processed": processed, "failed": failed}

    async def _run_filter_step(
        self, step: FilterStep, items: list[Any], ctx: StepContext, job_id: str, run_id: str
    ) -> dict[str, Any]:
        passed: list[Any] = []
        skipped = 0

        for item in items:
            unit = self._extract_work_unit(item)
            try:
                keep = await step.handler(item, ctx)
                if keep:
                    passed.append(item)
                    if unit:
                        self._store.record_progress(StepProgress(
                            unit_source_type=unit["source_type"], unit_source_id=unit["source_id"],
                            job_id=job_id, step_id=step.id, run_id=run_id, status="completed",
                            completed_at=_now(),
                        ))
                else:
                    skipped += 1
                    if unit:
                        self._store.record_progress(StepProgress(
                            unit_source_type=unit["source_type"], unit_source_id=unit["source_id"],
                            job_id=job_id, step_id=step.id, run_id=run_id, status="skipped",
                            completed_at=_now(),
                        ))
            except Exception as err:
                skipped += 1
                if unit:
                    self._store.record_progress(StepProgress(
                        unit_source_type=unit["source_type"], unit_source_id=unit["source_id"],
                        job_id=job_id, step_id=step.id, run_id=run_id, status="failed",
                        error=str(err), completed_at=_now(),
                    ))
                ctx.log(f'Filter step "{step.name}" error: {err}')

        return {"passed": passed, "skipped": skipped}

    async def _run_reduce_step(
        self, step: ReduceStep, items: list[Any], ctx: StepContext, job_id: str, run_id: str
    ) -> dict[str, Any]:
        try:
            output = await step.handler(items, ctx)
            self._store.record_progress(StepProgress(
                unit_source_type="_reduce", unit_source_id=step.id,
                job_id=job_id, step_id=step.id, run_id=run_id, status="completed",
                completed_at=_now(),
            ))
            return {"output": output}
        except Exception as err:
            self._store.record_progress(StepProgress(
                unit_source_type="_reduce", unit_source_id=step.id,
                job_id=job_id, step_id=step.id, run_id=run_id, status="failed",
                error=str(err), completed_at=_now(),
            ))
            ctx.log(f'Reduce step "{step.name}" failed: {err}')
            return {"error": str(err)}

    def _extract_work_unit(self, item: Any) -> dict[str, str] | None:
        if hasattr(item, "source_type") and hasattr(item, "source_id"):
            return {"source_type": item.source_type, "source_id": item.source_id}
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
