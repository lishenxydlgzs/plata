"""Bounded, persistent execution history with task-local log capture."""

from contextvars import ContextVar
import logging
import time

active_run = ContextVar('active_job_run', default=None)


class ExecutionHistory(logging.Handler):
    def __init__(self, db):
        super().__init__(logging.INFO)
        self.db = db
        self.setFormatter(logging.Formatter('%(message)s'))
        db.executescript('''
            CREATE TABLE IF NOT EXISTS job_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                trigger TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL,
                status TEXT NOT NULL, summary TEXT, error TEXT);
            CREATE INDEX IF NOT EXISTS job_runs_job ON job_runs(job_id, id DESC);
            CREATE TABLE IF NOT EXISTS job_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL,
                timestamp REAL NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS job_logs_run ON job_logs(run_id, id);
        ''')
        with db:
            db.execute("UPDATE job_runs SET status='interrupted', finished_at=?, "
                       "error='Server stopped before completion' WHERE status='running'", (time.time(),))

    def begin(self, job_id, trigger):
        with self.db:
            run_id = self.db.execute(
                "INSERT INTO job_runs(job_id,trigger,started_at,status) VALUES(?,?,?,'running')",
                (job_id, trigger, time.time()),
            ).lastrowid
            self.db.execute('''DELETE FROM job_logs WHERE run_id IN (
                SELECT id FROM job_runs WHERE job_id=? ORDER BY id DESC LIMIT -1 OFFSET 100)''', (job_id,))
            self.db.execute('''DELETE FROM job_runs WHERE job_id=? AND id NOT IN (
                SELECT id FROM job_runs WHERE job_id=? ORDER BY id DESC LIMIT 100)''', (job_id, job_id))
        return run_id

    def finish(self, run_id, status, summary=None, error=None):
        with self.db:
            self.db.execute('UPDATE job_runs SET finished_at=?,status=?,summary=?,error=? WHERE id=?',
                            (time.time(), status, summary, error, run_id))

    def emit(self, record):
        context = active_run.get()
        if context is None or context[0] is not self:
            return
        run_id = context[1]
        with self.db:
            self.db.execute('INSERT INTO job_logs(run_id,timestamp,level,message) VALUES(?,?,?,?)',
                            (run_id, record.created, record.levelname, self.format(record)[-2000:]))
            self.db.execute('''DELETE FROM job_logs WHERE run_id=? AND id NOT IN (
                SELECT id FROM job_logs WHERE run_id=? ORDER BY id DESC LIMIT 500)''', (run_id, run_id))

    def runs(self, job_id, before=None, limit=20):
        return [dict(row) for row in self.db.execute(
            'SELECT * FROM job_runs WHERE job_id=? AND id<? ORDER BY id DESC LIMIT ?',
            (job_id, before or 9223372036854775807, limit))]

    def detail(self, job_id, run_id):
        row = self.db.execute('SELECT * FROM job_runs WHERE id=? AND job_id=?', (run_id, job_id)).fetchone()
        if not row:
            return None
        return {**dict(row), 'logs': [dict(log) for log in self.db.execute(
            'SELECT timestamp,level,message FROM job_logs WHERE run_id=? ORDER BY id', (run_id,))]}
