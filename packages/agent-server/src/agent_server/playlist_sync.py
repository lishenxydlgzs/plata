"""Persistent, single-worker YouTube audio import jobs."""

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from . import media
from .job_history import ExecutionHistory, active_run
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
MAINTENANCE_ID = "quality-maintenance"


def slug(value: str) -> str:
    return re.sub(r"[^\w-]+", "_", value, flags=re.UNICODE).strip("_-")[:100] or "playlist"


class NewSyncJob(BaseModel):
    url: str
    name: str = Field(min_length=1, max_length=100)
    interval_hours: int = Field(default=24, ge=1, le=8760)

    @field_validator("url")
    @classmethod
    def youtube_playlist(cls, value: str) -> str:
        parsed = urlparse(value)
        playlist = parse_qs(parsed.query).get("list", [""])[0]
        if (parsed.scheme != "https" or parsed.netloc not in
                {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
                or parsed.path not in {"/playlist", "/watch"}
                or not re.fullmatch(r"[A-Za-z0-9_-]{10,100}", playlist)):
            raise ValueError("Provide an HTTPS YouTube playlist URL with a list ID")
        return f"https://www.youtube.com/playlist?list={playlist}"


class SyncSettings(BaseModel):
    interval_hours: int = Field(ge=1, le=8760)
    enabled: bool = True


async def run_downloader(*args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-progress",
        "--socket-timeout", "30", "--retries", "3", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=1800)
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="replace")[-1500:] or "yt-dlp failed")
    return stdout.decode()


class PlaylistSync:
    def __init__(self, db_path: Path | None = None, maintenance=None):
        self.db_path = db_path or Path(os.getenv("DB_DIR", "./data")) / "playlist-sync.sqlite3"
        self.db = None
        self.task = None
        self.maintenance = maintenance
        self.history = None
        self._active = False

    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, url TEXT NOT NULL, folder TEXT NOT NULL,
            interval_hours INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            next_run REAL NOT NULL DEFAULT 0, last_run REAL,
            state TEXT NOT NULL DEFAULT 'queued', imported INTEGER NOT NULL DEFAULT 0,
            error TEXT)""")
        self.db.execute("UPDATE jobs SET state='queued', next_run=0 WHERE state='running'")
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(jobs)")}
        if "queued_trigger" not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN queued_trigger TEXT NOT NULL DEFAULT 'scheduled'")
        if self.maintenance:
            tomorrow = datetime.now().date() + timedelta(days=1)
            midnight = datetime.combine(tomorrow, datetime.min.time()).timestamp()
            self.db.execute("""INSERT OR IGNORE INTO jobs
                (id,url,folder,interval_hours,next_run,state) VALUES(?, '', ?, 24, ?, 'idle')""",
                (MAINTENANCE_ID, "Daily quality improvement", midnight))
        self.db.commit()
        self.history = ExecutionHistory(self.db)

    def jobs(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM jobs ORDER BY id")]

    def get(self, job_id):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Sync job not found")
        return dict(row)

    def add(self, request: NewSyncJob):
        job_id = parse_qs(urlparse(request.url).query)["list"][0]
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO jobs(id,url,folder,interval_hours) VALUES(?,?,?,?)",
                    (job_id, request.url, f"{slug(request.name)}_{job_id}", request.interval_hours),
                )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "This playlist already has a sync job")
        return self.get(job_id)

    def settings(self, job_id: str, request: SyncSettings):
        self.get(job_id)
        with self.db:
            self.db.execute(
                "UPDATE jobs SET interval_hours=?,enabled=?,next_run=?, "
                "state=CASE WHEN state='queued' THEN 'idle' ELSE state END, "
                "queued_trigger='scheduled' WHERE id=?",
                (request.interval_hours, request.enabled, time.time() + request.interval_hours * 3600, job_id),
            )
        return self.get(job_id)

    def queue(self, job_id: str):
        job = self.get(job_id)
        if job["state"] == "running":
            raise HTTPException(409, "This playlist is already syncing")
        if not job["enabled"]:
            raise HTTPException(409, "Enable the job before syncing")
        with self.db:
            self.db.execute("UPDATE jobs SET next_run=0,state='queued',queued_trigger='manual' WHERE id=?", (job_id,))
        return self.get(job_id)

    async def sync(self, job, trigger=None):
        if self._active:
            raise HTTPException(409, "Another job is already running")
        self._active = True
        run_id = self.history.begin(job["id"], trigger or job.get("queued_trigger", "scheduled"))
        token = active_run.set((self.history, run_id))
        loggers = [logger, logging.getLogger("agent_server.maintenance")]
        for target in loggers:
            target.addHandler(self.history)
        with self.db:
            self.db.execute("UPDATE jobs SET state='running',last_run=?,error=NULL,queued_trigger='scheduled' WHERE id=?",
                            (time.time(), job["id"]))
        try:
            logger.info("Job started")
            if job["id"] == MAINTENANCE_ID:
                result = await self.maintenance.run_now()
                summary = f"Applied {result.get('total_actions', 0)} improvements across {result.get('iterations', 0)} review passes"
                with self.db:
                    self.db.execute("UPDATE jobs SET state='idle', next_run=? + interval_hours*3600 WHERE id=?",
                                    (time.time(), job["id"]))
            else:
                await self._sync_playlist(job)
                result = self.get(job["id"])
                summary = f"Imported {result['imported']} audio tracks"
                if result["error"]:
                    raise RuntimeError(result["error"])
            logger.info("Job completed: %s", summary)
            self.history.finish(run_id, "succeeded", summary)
            return result
        except asyncio.CancelledError:
            logger.warning("Job interrupted by server shutdown")
            self.history.finish(run_id, "interrupted", error="Server stopped before completion")
            with self.db:
                self.db.execute("UPDATE jobs SET state='queued',next_run=0 WHERE id=?", (job["id"],))
            raise
        except Exception as exc:
            error = str(exc)[-4000:]
            logger.error("Job failed: %s", error)
            self.history.finish(run_id, "failed", error=error)
            with self.db:
                self.db.execute("UPDATE jobs SET state='error',error=?,next_run=? + interval_hours*3600 WHERE id=?",
                                (error, time.time(), job["id"]))
            return {"error": error}
        finally:
            for target in loggers:
                target.removeHandler(self.history)
            active_run.reset(token)
            self._active = False

    async def _sync_playlist(self, job):
        with self.db:
            self.db.execute("UPDATE jobs SET state='running',last_run=?,error=NULL WHERE id=?",
                            (time.time(), job["id"]))
        imported = 0
        errors = []
        try:
            info = json.loads(await run_downloader("--flat-playlist", "--dump-single-json", job["url"]))
            folder = media.MEDIA_DIR / job["folder"]
            folder.mkdir(parents=True, exist_ok=True)
            staging = media.MEDIA_DIR / ".sync-staging"
            staging.mkdir(exist_ok=True)
            logger.info("Playlist loaded; checking for new audio")
            for entry in info.get("entries", []):
                if not entry:
                    continue
                video_id = entry.get("id", "")
                if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                    continue
                if any(folder.glob(f"*__{video_id}.mp3")):
                    logger.info("Already imported: %s", video_id)
                    continue
                try:
                    with tempfile.TemporaryDirectory(dir=staging) as temp:
                        target = Path(temp) / "audio.mp3"
                        await run_downloader(
                            "--no-playlist", "-f", "bestaudio/best", "--extract-audio",
                            "--audio-format", "mp3", "--audio-quality", "192K",
                            "--embed-metadata", "-o", str(Path(temp) / "audio.%(ext)s"),
                            f"https://www.youtube.com/watch?v={video_id}",
                        )
                        if not target.is_file() or target.stat().st_size == 0:
                            raise RuntimeError("Conversion produced no audio")
                        target.replace(folder / f"{slug(entry.get('title') or video_id)}__{video_id}.mp3")
                    logger.info("Imported audio: %s", video_id)
                    imported += 1
                    media.invalidate_playlist_cache()
                except Exception as exc:
                    logger.warning("Import failed for %s: %s", video_id, str(exc)[-1500:])
                    errors.append(f"{video_id}: {str(exc)[-1500:]}")
        except Exception as exc:
            errors.append(str(exc)[-1500:])
        with self.db:
            self.db.execute("""UPDATE jobs SET state=?,imported=?,error=?,
                next_run=? + interval_hours * 3600 WHERE id=?""",
                ("error" if errors else "idle", imported, "\n".join(errors)[-4000:] or None,
                 time.time(), job["id"]))

    async def worker(self):
        while True:
            try:
                for job in self.jobs():
                    # Read current settings after earlier jobs finish.
                    job = self.get(job["id"])
                    if job["enabled"] and job["next_run"] <= time.time() and not self._active:
                        await self.sync(job)
            except Exception:
                logger.exception("Playlist sync worker failed")
            await asyncio.sleep(60)

    def start(self):
        self.connect()
        self.task = asyncio.create_task(self.worker())

    async def stop(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        if self.db:
            self.db.close()


def sync_router(service: PlaylistSync) -> APIRouter:
    router = APIRouter(prefix="/media/sync-jobs", tags=["Media playlist sync"])

    @router.get("")
    async def list_jobs():
        return [job for job in service.jobs() if job["id"] != MAINTENANCE_ID]

    @router.post("", status_code=201)
    async def add_job(request: NewSyncJob):
        return service.add(request)

    @router.put("/{job_id}")
    async def update_job(job_id: str, request: SyncSettings):
        return service.settings(job_id, request)

    @router.post("/{job_id}/sync", status_code=202)
    async def sync_now(job_id: str):
        return service.queue(job_id)

    return router


def jobs_router(service: PlaylistSync) -> APIRouter:
    router = APIRouter(prefix="/api/jobs", tags=["Jobs"])

    @router.get("")
    async def list_jobs():
        return [{**job, "name": job["folder"] if job["id"] == MAINTENANCE_ID else job["folder"].removesuffix("_" + job["id"]).replace("_", " "),
                 "kind": "maintenance" if job["id"] == MAINTENANCE_ID else "playlist"}
                for job in service.jobs()]

    @router.put("/{job_id}")
    async def update_job(job_id: str, request: SyncSettings):
        return service.settings(job_id, request)

    @router.post("/{job_id}/run", status_code=202)
    async def run_job(job_id: str):
        return service.queue(job_id)

    @router.get("/{job_id}/runs")
    async def history(job_id: str, before: int | None = Query(default=None, ge=1),
                      limit: int = Query(default=20, ge=1, le=100)):
        service.get(job_id)
        return service.history.runs(job_id, before, limit)

    @router.get("/{job_id}/runs/{run_id}")
    async def execution(job_id: str, run_id: int):
        service.get(job_id)
        run = service.history.detail(job_id, run_id)
        if run is None:
            raise HTTPException(404, "Execution not found")
        return run

    return router
