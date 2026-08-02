"""Tests for Scheduler and parse_cadence."""

import pytest

from ontology.pipeline import parse_cadence


def test_parse_seconds():
    assert parse_cadence("30s") == 30_000


def test_parse_minutes():
    assert parse_cadence("5m") == 300_000


def test_parse_hours():
    assert parse_cadence("1h") == 3_600_000


def test_parse_days():
    assert parse_cadence("2d") == 172_800_000


def test_parse_raw_ms():
    assert parse_cadence("5000") == 5000


def test_parse_invalid_raises():
    with pytest.raises(ValueError):
        parse_cadence("invalid")

    with pytest.raises(ValueError):
        parse_cadence("abc")
