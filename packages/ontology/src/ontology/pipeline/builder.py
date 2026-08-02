"""Fluent builder for pipeline job definitions."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .types import Connector, FilterStep, JobDefinition, MapStep, ReduceStep, Step, StepContext


class JobBuilder:
    def __init__(self, id: str) -> None:
        self._id = id
        self._name = id
        self._connector: Connector | None = None
        self._steps: list[Step] = []
        self._cadence: str = "30m"

    def name(self, name: str) -> JobBuilder:
        self._name = name
        return self

    def from_connector(self, connector: Connector) -> JobBuilder:
        self._connector = connector
        return self

    def map(self, *, id: str, name: str, handler: Callable[[Any, StepContext], Awaitable[Any]]) -> JobBuilder:
        self._steps.append(MapStep(id=id, name=name, handler=handler))
        return self

    def filter(self, *, id: str, name: str, handler: Callable[[Any, StepContext], Awaitable[bool]]) -> JobBuilder:
        self._steps.append(FilterStep(id=id, name=name, handler=handler))
        return self

    def reduce(self, *, id: str, name: str, handler: Callable[[list[Any], StepContext], Awaitable[Any]]) -> JobBuilder:
        self._steps.append(ReduceStep(id=id, name=name, handler=handler))
        return self

    def cadence(self, cadence: str) -> JobBuilder:
        self._cadence = cadence
        return self

    def build(self) -> JobDefinition:
        if not self._connector:
            raise ValueError(f'Job "{self._id}" requires a connector. Call .from_connector(connector) before .build().')
        if not self._steps:
            raise ValueError(f'Job "{self._id}" requires at least one step.')
        return JobDefinition(
            id=self._id,
            name=self._name,
            cadence=self._cadence,
            connector=self._connector,
            steps=self._steps,
        )


def pipeline(id: str) -> JobBuilder:
    """Create a new pipeline job builder.

    Example::

        job = (
            pipeline("my-job")
            .from_connector(connector)
            .map(id="step1", name="Extract", handler=extract_fn)
            .filter(id="step2", name="Filter noise", handler=filter_fn)
            .reduce(id="step3", name="Deduplicate", handler=dedup_fn)
            .cadence("30m")
            .build()
        )
    """
    return JobBuilder(id)
