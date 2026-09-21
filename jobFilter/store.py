"""SQLite collection of every job that passed the rules, deduped across runs."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from jobFilter.models import Job
from jobFilter.providers import APPLICATION_ENGINES, CLAUDE, CODEX, LEGACY_CODEX, validate_engine

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
    via           TEXT NOT NULL DEFAULT 'hiringcafe',
    norm_url      TEXT,
    norm_key      TEXT,
    job_json      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_dedup ON jobs(dedup_key);
CREATE INDEX IF NOT EXISTS jobs_first_seen ON jobs(first_seen);
CREATE INDEX IF NOT EXISTS jobs_first_seen_date ON jobs(first_seen_date);
CREATE TABLE IF NOT EXISTS applications (
    job_id        TEXT PRIMARY KEY REFERENCES jobs(id),
    status        TEXT NOT NULL DEFAULT 'queued',
    engine        TEXT NOT NULL DEFAULT 'claude-chrome',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    session_id    TEXT,
    summary       TEXT,
    page_url      TEXT,
    screenshot    TEXT,
    log_path      TEXT,
    note          TEXT,
    result_json   TEXT,
    settings_json TEXT,
    submitted_at TEXT,
    submitted_at_estimated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS applications_status ON applications(status);
CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    question    TEXT NOT NULL,
    options     TEXT,
    answer      TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS questions_status ON questions(status);
CREATE TABLE IF NOT EXISTS screenings (
    job_id        TEXT PRIMARY KEY REFERENCES jobs(id),
    status        TEXT NOT NULL,            -- ok | failed
    verdict       TEXT,                     -- likely | unlikely | unknown  (will this employer sponsor?)
    statement     TEXT,                     -- what the posting itself says: sponsors | no_sponsorship | not_mentioned
    requires_citizenship INTEGER,
    new_grad_fit  INTEGER,
    fit_score     INTEGER,
    summary       TEXT,
    evidence_json TEXT,
    sources_json  TEXT,
    model         TEXT,
    cost_usd      REAL,
    screened_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS company_sponsorship (
    company_key   TEXT PRIMARY KEY,         -- normalized company name
    company       TEXT,
    verdict       TEXT,                     -- likely | unlikely | unknown
    evidence_json TEXT,
    sources_json  TEXT,
    updated_at    TEXT NOT NULL
);
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


def _iso() -> str:
    return utc_now().isoformat(timespec="seconds")


NORM_KEY_VERSION = 2  # 1: company+title, 2: company+title+location

APP_STATUSES = ("queued", "running", "review_ready", "needs_answer", "needs_login", "captcha",
                "already_applied", "failed", "submitted", "skipped")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        """Add columns introduced after the first release and backfill them."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(jobs)")}
        added = False
        for col, ddl in (("via", "TEXT NOT NULL DEFAULT 'hiringcafe'"), ("norm_url", "TEXT"), ("norm_key", "TEXT")):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")
                added = True
        # user_version tracks the norm_key formula; bump NORM_KEY_VERSION when it changes to force a recompute
        stale_formula = self.conn.execute("PRAGMA user_version").fetchone()[0] < NORM_KEY_VERSION
        if added or stale_formula or self.conn.execute("SELECT COUNT(*) FROM jobs WHERE norm_key IS NULL").fetchone()[0]:
            where = "" if stale_formula else " WHERE norm_key IS NULL"
            for row in self.conn.execute(f"SELECT id, job_json FROM jobs{where}").fetchall():
                j = json.loads(row["job_json"])
                job = Job(**{k: v for k, v in j.items() if k in Job.__dataclass_fields__ and k != "raw"})
                self.conn.execute("UPDATE jobs SET norm_url = ?, norm_key = ?, via = ? WHERE id = ?",
                                  (job.norm_url(), job.norm_key(), job.via, row["id"]))
            self.conn.executescript("CREATE INDEX IF NOT EXISTS jobs_norm_url ON jobs(norm_url); CREATE INDEX IF NOT EXISTS jobs_norm_key ON jobs(norm_key);")
            self.conn.execute(f"PRAGMA user_version = {NORM_KEY_VERSION}")
            self.conn.commit()

        if "settings_json" not in {r[1] for r in self.conn.execute("PRAGMA table_info(applications)")}:
            self.conn.execute("ALTER TABLE applications ADD COLUMN settings_json TEXT")
            self.conn.commit()

        app_columns = {r[1] for r in self.conn.execute("PRAGMA table_info(applications)")}
        if "submitted_at" not in app_columns:
            with self.conn:
                self.conn.execute("ALTER TABLE applications ADD COLUMN submitted_at TEXT")
                self.conn.execute("ALTER TABLE applications ADD COLUMN submitted_at_estimated INTEGER NOT NULL DEFAULT 0")
                # Older releases tracked only the last update, not the actual submission.
                self.conn.execute("UPDATE applications SET submitted_at=updated_at, submitted_at_estimated=1 WHERE status='submitted'")

        # Retire manual Codex claims without starting work in a different browser.
        # Results, artifacts, answers and completed statuses remain intact.
        with self.conn:
            self.conn.execute("""UPDATE applications SET engine=?, session_id=NULL,
                summary=CASE WHEN status IN ('queued','running') THEN ? ELSE summary END,
                status=CASE WHEN status IN ('queued','running') THEN 'failed' ELSE status END
                WHERE engine=?""", (CODEX,
                "Manual Codex was retired. Click Run again to continue with the automatic browser; sign in there if needed.",
                LEGACY_CODEX))

    # ----- writes --------------------------------------------------------
    def upsert_many(self, jobs: list[Job], run_id: str) -> list[Job]:
        """Insert unseen jobs, touch seen ones. Returns the jobs that were new."""
        now = utc_now()
        now_iso = now.isoformat(timespec="seconds")
        local_date = now.astimezone().strftime("%Y-%m-%d")
        new: list[Job] = []
        cur = self.conn.cursor()
        for job in jobs:
            nurl, nkey = job.norm_url(), job.norm_key()
            # same posting seen before: by id, by hiring.cafe's dedup cluster, by apply URL, or (only across
            # sources; within one source the id is authoritative) by company+title+location
            row = cur.execute(
                "SELECT id FROM jobs WHERE id = ? OR dedup_key = ? OR (norm_url IS NOT NULL AND norm_url = ?) "
                "OR (norm_key = ? AND norm_key != '' AND via != ?)",
                (job.id, job.dedup_key, nurl, nkey, job.via)).fetchone()
            if row:
                cur.execute("UPDATE jobs SET last_seen = ?, seen_count = seen_count + 1 WHERE id = ?",
                            (now_iso, row["id"]))
                continue
            cur.execute(
                """INSERT INTO jobs (id, dedup_key, first_seen, first_seen_date, last_seen, first_run_id,
                                     published_at, published_millis, title, company, location,
                                     apply_url, hc_url, visa_hint, via, norm_url, norm_key, job_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job.id, job.dedup_key, now_iso, local_date, now_iso, run_id,
                 job.published_at, job.published_millis, job.title, job.company, job.location,
                 job.apply_url, job.hc_url,
                 None if job.visa_sponsorship is None else int(job.visa_sponsorship),
                 job.via, nurl, nkey, json.dumps(job.to_dict())),
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
        rows = [self._row(r) for r in self.conn.execute(sql, params).fetchall()]
        screens = self.screening_map([r["id"] for r in rows]) if rows else {}
        for r in rows:
            r["screening"] = screens.get(r["id"])
        return rows

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

    # ----- screenings ------------------------------------------------------
    def save_screening(self, job_id: str, data: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO screenings (job_id, status, verdict, statement, requires_citizenship, new_grad_fit,
                                                  fit_score, summary, evidence_json, sources_json, model, cost_usd, screened_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, data.get("status", "ok"), data.get("verdict"), data.get("statement"),
             None if data.get("requires_citizenship") is None else int(bool(data["requires_citizenship"])),
             None if data.get("new_grad_fit") is None else int(bool(data["new_grad_fit"])),
             data.get("fit_score"), data.get("summary"), json.dumps(data.get("evidence") or []),
             json.dumps(data.get("sources") or []), data.get("model"), data.get("cost_usd"), _iso()))
        self.conn.commit()

    def screening_map(self, job_ids: Optional[list[str]] = None) -> dict[str, dict[str, Any]]:
        sql = "SELECT * FROM screenings"
        params: list[Any] = []
        if job_ids is not None:
            if not job_ids:
                return {}
            sql += f" WHERE job_id IN ({','.join('?' * len(job_ids))})"
            params = list(job_ids)
        out = {}
        for r in self.conn.execute(sql, params).fetchall():
            d = dict(r)
            d["evidence"] = json.loads(d.pop("evidence_json") or "[]")
            d["sources"] = json.loads(d.pop("sources_json") or "[]")
            out[d["job_id"]] = d
        return out

    def unscreened(self, limit: int = 50, since_hours: Optional[float] = None) -> list[dict[str, Any]]:
        sql = "SELECT j.* FROM jobs j LEFT JOIN screenings s ON s.job_id = j.id WHERE s.job_id IS NULL"
        params: list[Any] = []
        if since_hours is not None:
            sql += " AND j.first_seen >= ?"
            params.append((utc_now() - timedelta(hours=since_hours)).isoformat(timespec="seconds"))
        sql += " ORDER BY j.first_seen DESC LIMIT ?"
        params.append(limit)
        return [self._row(r) for r in self.conn.execute(sql, params).fetchall()]

    def company_sponsorship(self, company_key: str, max_age_days: float = 30) -> Optional[dict[str, Any]]:
        r = self.conn.execute("SELECT * FROM company_sponsorship WHERE company_key = ?", (company_key,)).fetchone()
        if not r:
            return None
        age = (utc_now() - datetime.fromisoformat(r["updated_at"])).total_seconds() / 86400
        if age > max_age_days:
            return None
        d = dict(r)
        d["evidence"] = json.loads(d.pop("evidence_json") or "[]")
        d["sources"] = json.loads(d.pop("sources_json") or "[]")
        return d

    def save_company_sponsorship(self, company_key: str, company: str, verdict: str,
                                 evidence: list[str], sources: list[str]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO company_sponsorship (company_key, company, verdict, evidence_json, sources_json, updated_at) VALUES (?,?,?,?,?,?)",
            (company_key, company, verdict, json.dumps(evidence), json.dumps(sources), _iso()))
        self.conn.commit()

    # ----- applications --------------------------------------------------
    def application_launch_defaults(self, defaults: dict[str, Any]) -> dict[str, Any]:
        """Preselect the last launch choices, keeping each provider's own model.

        Use creation order: retrying or updating an old application must not
        replace the choices from a more recently started application.
        """
        result = dict(defaults)
        latest = []
        for engine, keys in ((CLAUDE, ("model",)), (CODEX, ("codex_model", "codex_reasoning_effort"))):
            row = self.conn.execute(
                "SELECT engine, settings_json, created_at, rowid FROM applications "
                "WHERE engine=? AND settings_json IS NOT NULL AND settings_json != 'null' "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (engine,)).fetchone()
            if row:
                settings = json.loads(row["settings_json"])
                result.update({key: settings[key] for key in keys if key in settings})
                latest.append(row)
        if latest:
            result["engine"] = max(latest, key=lambda r: (r["created_at"], r["rowid"]))["engine"]
        return result

    def queue_application(self, job_id: str, engine: str = CLAUDE, settings: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        validate_engine(engine)
        existing = self.get_application(job_id)
        if existing and existing["engine"] != engine:
            raise ValueError("An existing application keeps its original engine; use Retry to continue it.")
        if existing and existing["status"] == "running":
            raise ValueError("Application is already running")
        from jobFilter.profile import application_settings
        if existing and settings is not None:
            raise ValueError("Existing applications keep their launch settings; use Retry to continue.")
        chosen = existing.get("settings") if existing else application_settings({**(settings or {}), "engine": engine})
        if not existing and engine == CODEX and not chosen["codex_model"]:
            raise ValueError("Choose a GPT model before starting the application")
        now = _iso()
        self.conn.execute(
            """INSERT INTO applications (job_id, status, engine, created_at, updated_at, settings_json)
               VALUES (?, 'queued', ?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET status='queued', engine=excluded.engine, updated_at=excluded.updated_at""",
            (job_id, engine, now, now, json.dumps(chosen) if chosen else None))
        self.conn.commit()
        return self.get_application(job_id)

    def update_application(self, job_id: str, **fields: Any) -> None:
        if "status" in fields and fields["status"] not in APP_STATUSES:
            raise ValueError(f"bad status {fields['status']}")
        if "result" in fields:
            fields["result_json"] = json.dumps(fields.pop("result"))
        fields["updated_at"] = _iso()
        if fields.get("status") == "submitted":
            current = self.get_application(job_id)
            if current and current["status"] != "submitted":
                fields["submitted_at"] = fields["updated_at"]
                fields["submitted_at_estimated"] = 0
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE applications SET {cols} WHERE job_id = ?", (*fields.values(), job_id))
        self.conn.commit()

    def bump_attempts(self, job_id: str) -> None:
        self.conn.execute("UPDATE applications SET attempts = attempts + 1, updated_at = ? WHERE job_id = ?", (_iso(), job_id))
        self.conn.commit()

    def get_application(self, job_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT a.*, j.job_json, j.hc_url FROM applications a JOIN jobs j ON j.id = a.job_id WHERE a.job_id = ?",
            (job_id,)).fetchone()
        return self._app_row(row) if row else None

    def list_applications(self, status: Optional[str] = None) -> list[dict[str, Any]]:
        sql = "SELECT a.*, j.job_json, j.hc_url FROM applications a JOIN jobs j ON j.id = a.job_id"
        params: list[Any] = []
        if status:
            sql += " WHERE a.status = ?"
            params.append(status)
        sql += " ORDER BY a.submitted_at IS NULL, a.submitted_at DESC, a.updated_at DESC, a.job_id"
        return [self._app_row(r) for r in self.conn.execute(sql, params).fetchall()]

    def next_queued(self, engine: Optional[str] = None) -> Optional[dict[str, Any]]:
        engines = (validate_engine(engine),) if engine is not None else APPLICATION_ENGINES
        row = self.conn.execute(
            "SELECT a.*, j.job_json, j.hc_url FROM applications a JOIN jobs j ON j.id = a.job_id "
            f"WHERE a.status = 'queued' AND a.engine IN ({','.join('?' for _ in engines)}) ORDER BY a.updated_at ASC LIMIT 1", engines).fetchone()
        return self._app_row(row) if row else None

    def application_counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) FROM applications GROUP BY status").fetchall()
        return {r[0]: r[1] for r in rows}

    # ----- questions -------------------------------------------------------
    def add_question(self, job_id: str, question: str, options: Optional[list[str]] = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO questions (job_id, question, options, created_at) VALUES (?,?,?,?)",
            (job_id, question, json.dumps(options) if options else None, _iso()))
        self.conn.commit()
        return cur.lastrowid

    def answer_question(self, question_id: int, answer: str) -> Optional[dict[str, Any]]:
        self.conn.execute("UPDATE questions SET answer = ?, status = 'answered' WHERE id = ?", (answer, question_id))
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
        return self._q_row(row) if row else None

    def questions(self, job_id: Optional[str] = None, status: Optional[str] = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM questions", []
        clauses = []
        if job_id:
            clauses.append("job_id = ?"); params.append(job_id)
        if status:
            clauses.append("status = ?"); params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id"
        return [self._q_row(r) for r in self.conn.execute(sql, params).fetchall()]

    @staticmethod
    def _q_row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["options"] = json.loads(d["options"]) if d.get("options") else None
        return d

    @staticmethod
    def _app_row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["settings"] = json.loads(d.pop("settings_json", None) or "null")
        d["job"] = json.loads(d.pop("job_json"))
        d["result"] = json.loads(d["result_json"]) if d.get("result_json") else None
        d.pop("result_json", None)
        return d

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["job"] = json.loads(d.pop("job_json"))
        return d
