"""Tests for media catalog safety."""

from pathlib import Path

from agent_server import media


def test_playlist_scan_skips_unsafe_double_dot_filename(
    tmp_path: Path, monkeypatch,
):
    week = tmp_path / "cc_cycle3" / "week_1"
    week.mkdir(parents=True)
    (week / "safe.mp3").write_bytes(b"fake")
    (week / "unsafe..mp3").write_bytes(b"fake")
    monkeypatch.setattr(media, "MEDIA_DIR", tmp_path)
    media._playlist_cache = None

    tracks = media.scan_playlist_catalog()["cc_cycle3_week_1"]

    assert [track["file"] for track in tracks] == ["cc_cycle3/week_1/safe.mp3"]
