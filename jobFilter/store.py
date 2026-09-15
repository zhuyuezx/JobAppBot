"""SQLite collection of every job that passed the rules, deduped across runs."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from jobFilter.models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    dedup_key     TEXT,
    first_seen    TEXT NOT NULL,        -- UTC ISO
    first_seen_date TEXT NOT NULL,      -- local YYYY-MM-DD, used for daily views/excel
    last_seen     TEXT NOT NULL,
    seen_count    INTEGER NOT NULL DEFAULT 1,
    first_run_id  TEXT NOT NULL,
    published_at  TEXT,
    published_millis INTEGER,
    title         TEXT,
    company       TEXT,
    location      TEXT,
    apply_url     TEXT,
    hc_url        TEXT,
    visa_hint     INTEGER,
    job_json      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_dedup ON jobs(dedup_key);
CREATE INDEX IF NOT EXISTS jobs_first_seen ON jobs(first_seen);
CREATE INDEX IF NOT EXISTS jobs_first_seen_date ON jobs(first_seen_date);
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    fetched     INTEGER,
    kept        INTEGER,
    new_jobs    INTEGER,
    search_state_json TEXT
);
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ----- writes --------------------------------------------------------
    def upsert_many(self, jobs: list[Job], run_id: str) -> list[Job]:
        """Insert unseen jobs, touch seen ones. Returns the jobs that were new."""
        now = utc_now()
        now_iso = now.isoformat(timespec="seconds")
        local_date = now.astimezone().strftime("%Y-%m-%d")
        new: list[Job] = []
        cur = self.conn.cursor()
        for job in jobs:
            row = cur.execute("SELECT id FROM jobs WHERE id = ? OR dedup_key = ?",
                              (job.id, job.dedup_key)).fetchone()
            if row:
                cur.execute("UPDATE jobs SET last_seen = ?, seen_count = seen_count + 1 WHERE id = ?",
                            (now_iso, row["id"]))
                continue
            cur.execute(
                """INSERT INTO jobs (id, dedup_key, first_seen, first_seen_date, last_seen, first_run_id,
                                     published_at, published_millis, title, company, location,
                                     apply_url, hc_url, visa_hint, job_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job.id, job.dedup_key, now_iso, local_date, now_iso, run_id,
                 job.published_at, job.published_millis, job.title, job.company, job.location,
                 job.apply_url, job.hc_url,
                 None if job.visa_sponsorship is None else int(job.visa_sponsorship),
                 json.dumps(job.to_dict())),
            )
            new.append(job)
        self.conn.commit()
        return new

    def record_run(self, run_id: str, search_state: dict[str, Any], fetched: int, kept: int, new_jobs: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, started_at, fetched, kept, new_jobs, search_state_json) VALUES (?,?,?,?,?,?)",
            (run_id, utc_now().isoformat(timespec="seconds"), fetched, kept, new_jobs, json.dumps(search_state)),
        )
        self.conn.commit()

    # ----- reads ---------------------------------------------------------
    def query(self, since_hours: Optional[float] = None, date: Optional[str] = None,
              limit: Optional[int] = None) -> list[dict[str, Any]]:
        """Jobs first seen in the last `since_hours`, or on local `date` (YYYY-MM-DD), or all."""
        sql = "SELECT * FROM jobs"
        params: list[Any] = []
        if since_hours is not None:
            sql += " WHERE first_seen >= ?"
            params.append((utc_now() - timedelta(hours=since_hours)).isoformat(timespec="seconds"))
        elif date:
            sql += " WHERE first_seen_date = ?"
            params.append(date)
        sql += " ORDER BY first_seen DESC, published_millis DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._row(r) for r in self.conn.execute(sql, params).fetchall()]

    def dates(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT first_seen_date AS date, COUNT(*) AS count FROM jobs GROUP BY first_seen_date ORDER BY date DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT run_id, started_at, fetched, kept, new_jobs FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["job"] = json.loads(d.pop("job_json"))
        return d
