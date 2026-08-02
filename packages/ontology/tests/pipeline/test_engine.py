"""Tests for PipelineEngine."""

import sqlite3
from datetime import datetime, timezone

import pytest

from ontology.pipeline import (
    PipelineEngine,
    PipelineStore,
    initialize_pipeline_db,
    pipeline,
    EngineOptions,
)
from ontology.pipeline.types import Connector, StepContext, WorkUnit


class MockConnector:
    id = "mock"
    source_type = "test"

    def __init__(self, items: list[WorkUnit]):
        self._items = items

    async def poll(self, since: datetime | None) -> list[WorkUnit]:
        return self._items


@pytest.fixture
def store():
    db = sqlite3.connect(":memory:")
    initialize_pipeline_db(db)
    return PipelineStore(db)


@pytest.fixture
def engine(store):
    return PipelineEngine(EngineOptions(store=store))


def make_units(count: int) -> list[WorkUnit]:
    return [
        WorkUnit(source_type="test", source_id=f"item-{i}", payload={"value": i}, timestamp=datetime.now(timezone.utc))
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_map_step(engine, store):
    units = make_units(3)
    connector = MockConnector(units)

    async def double(item: WorkUnit, ctx: StepContext):
        return {"id": item.source_id, "doubled": item.payload["value"] * 2}

    job = (
        pipeline("test-map")
        .from_connector(connector)
        .map(id="double", name="Double Values", handler=double)
        .cadence("1h")
        .build()
    )

    result = await engine.run(job)
    assert result.status == "completed"
    assert result.units_processed == 3
    assert result.units_failed == 0


@pytest.mark.asyncio
async def test_filter_step(engine, store):
    units = make_units(5)
    connector = MockConnector(units)

    async def keep_even(item: WorkUnit, ctx: StepContext) -> bool:
        return item.payload["value"] % 2 == 0

    job = (
        pipeline("test-filter")
        .from_connector(connector)
        .filter(id="even", name="Keep Even", handler=keep_even)
        .cadence("1h")
        .build()
    )

    result = await engine.run(job)
    assert result.status == "completed"
    assert result.units_skipped == 2  # items 1, 3
    assert result.units_processed == 3  # items 0, 2, 4


@pytest.mark.asyncio
async def test_reduce_step(engine, store):
    units = make_units(3)
    connector = MockConnector(units)

    async def sum_values(items: list, ctx: StepContext):
        return {"total": sum(getattr(i, "payload", i).get("value", 0) if hasattr(i, "payload") else 0 for i in items)}

    async def passthrough(item: WorkUnit, ctx: StepContext):
        return item

    job = (
        pipeline("test-reduce")
        .from_connector(connector)
        .map(id="pass", name="Passthrough", handler=passthrough)
        .reduce(id="sum", name="Sum", handler=sum_values)
        .cadence("1h")
        .build()
    )

    result = await engine.run(job)
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_map_step_failure_continues(engine, store):
    units = make_units(3)
    connector = MockConnector(units)

    async def fail_on_1(item: WorkUnit, ctx: StepContext):
        if item.payload["value"] == 1:
            raise ValueError("intentional failure")
        return item

    job = (
        pipeline("test-fail")
        .from_connector(connector)
        .map(id="maybe-fail", name="Maybe Fail", handler=fail_on_1)
        .cadence("1h")
        .build()
    )

    result = await engine.run(job)
    assert result.status == "completed"
    assert result.units_processed == 2
    assert result.units_failed == 1


@pytest.mark.asyncio
async def test_empty_connector(engine, store):
    connector = MockConnector([])

    async def noop(item, ctx):
        return item

    job = (
        pipeline("test-empty")
        .from_connector(connector)
        .map(id="noop", name="Noop", handler=noop)
        .cadence("1h")
        .build()
    )

    result = await engine.run(job)
    assert result.status == "completed"
    assert result.units_processed == 0
