"""Tests for PipelineStore."""

import sqlite3

import pytest

from ontology.pipeline import PipelineStore, initialize_pipeline_db
from ontology.pipeline.types import StepProgress


@pytest.fixture
def store():
    db = sqlite3.connect(":memory:")
    initialize_pipeline_db(db)
    return PipelineStore(db)


def test_register_and_get_job(store: PipelineStore):
    store.register_job("job-1", "Test Job", "30m", "connector-1")
    job = store.get_job("job-1")
    assert job is not None
    assert job["name"] == "Test Job"
    assert job["cadence"] == "30m"


def test_list_jobs(store: PipelineStore):
    store.register_job("job-1", "First", "30m", "c1")
    store.register_job("job-2", "Second", "1h", "c2")
    jobs = store.list_jobs()
    assert len(jobs) == 2


def test_unregister_job(store: PipelineStore):
    store.register_job("job-1", "Test", "30m", "c1")
    assert store.unregister_job("job-1")
    assert store.get_job("job-1") is None


def test_create_and_complete_run(store: PipelineStore):
    run = store.create_run("run-1", "job-1")
    assert run.status == "running"

    store.complete_run("run-1", "completed", 10, 2)
    fetched = store.get_run("run-1")
    assert fetched is not None
    assert fetched.status == "completed"
    assert fetched.units_processed == 10
    assert fetched.units_failed == 2


def test_get_runs_for_job(store: PipelineStore):
    store.create_run("run-1", "job-1")
    store.create_run("run-2", "job-1")
    store.create_run("run-3", "job-2")

    runs = store.get_runs_for_job("job-1")
    assert len(runs) == 2


def test_record_and_get_progress(store: PipelineStore):
    progress = StepProgress(
        unit_source_type="test",
        unit_source_id="item-1",
        job_id="job-1",
        step_id="step-1",
        run_id="run-1",
        status="completed",
        completed_at="2024-01-01T00:00:00Z",
    )
    store.record_progress(progress)

    fetched = store.get_progress("job-1", "step-1", "test", "item-1")
    assert fetched is not None
    assert fetched.status == "completed"


def test_get_unprocessed(store: PipelineStore):
    store.record_progress(StepProgress(
        unit_source_type="test", unit_source_id="item-1",
        job_id="job-1", step_id="step-1", run_id="run-1",
        status="completed", completed_at="2024-01-01T00:00:00Z",
    ))

    unprocessed = store.get_unprocessed("job-1", "step-1", "test", ["item-1", "item-2", "item-3"])
    assert unprocessed == ["item-2", "item-3"]


def test_run_logs(store: PipelineStore):
    store.add_run_log("run-1", "step-1", "/tmp/log.txt", label="extraction")
    logs = store.get_run_logs("run-1")
    assert len(logs) == 1
    assert logs[0].log_path == "/tmp/log.txt"
    assert logs[0].label == "extraction"
