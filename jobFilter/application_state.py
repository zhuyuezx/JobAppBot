"""Application result contract, artifacts and atomic Codex claims. No browser dependencies."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from jsonschema import validate

from jobFilter import profile
from jobFilter.providers import CODEX
from jobFilter.store import _iso


APPLY_DIR = Path(__file__).resolve().parent.parent / "data" / "apply"

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["review_ready", "needs_answer", "needs_login", "captcha", "already_applied", "unavailable", "failed"]},
        "summary": {"type": "string", "description": "What was filled, what is left, and any blocker. Never include passwords or authentication secrets."},
        "page_url": {"type": "string", "description": "URL of the tab where the application currently is."},
        "unanswered_questions": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "why": {"type": "string"}}, "required": ["question"]},
        },
        "filled_fields": {"type": "array", "items": {"type": "string"}},
        "screenshot_path": {"type": "string"},
        "lessons": {"type": "array", "items": {"type": "string"},
                    "description": "New, reusable facts about this site's form that are NOT already in the skill notes (selectors, quirks, required questions). Empty if nothing new."},
    },
    "required": ["status", "summary", "page_url", "unanswered_questions"],
}


def work_directory(job_id: str, create: bool = True) -> Path:
    d = APPLY_DIR / re.sub(r"[^A-Za-z0-9_.-]+", "_", job_id)[:120]
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def structured_result(event):
    """Read Claude's structured result, including its JSON-text fallback."""
    if event.get("structured_output"):
        return event["structured_output"]
    text = event.get("result") or ""
    try:
        return json.loads(text) if text.lstrip().startswith("{") else {}
    except ValueError:
        return {}


def claim_application(store, job_id):
    # Prepare local context before claiming, so a bad profile cannot strand a job.
    current = store.get_application(job_id)
    if not current:
        raise ValueError("Unknown application")
    resume = profile.application_resume(current["job"], (current.get("settings") or {}).get("resume"))
    context = {"profile": profile.load_profile(), "answer_bank": profile.load_answers(),
               "resume_path": str(resume["path"] or ""), "resume_text": resume["text"],
               "resume_version": f"{resume['version'].upper()} ({resume['reason']})"}
    work = work_directory(job_id)
    with store.conn:
        store.conn.execute("BEGIN IMMEDIATE")
        if store.conn.execute("SELECT 1 FROM applications WHERE engine=? AND status='running'", (CODEX,)).fetchone():
            raise ValueError("A Codex application is running. Finish it, or cancel and retry it in the UI before claiming another.")
        app = store.get_application(job_id)
        if not app:
            raise ValueError("Unknown application")
        if app["engine"] != CODEX or app["status"] != "queued":
            raise ValueError("Application is no longer queued for this provider")
        token = str(uuid.uuid4())
        store.conn.execute("UPDATE applications SET status='running', session_id=?, attempts=attempts+1, updated_at=? WHERE job_id=?",
                           (token, _iso(), app["job_id"]))
    return {"job_id": app["job_id"], "claim_token": token, "job": app["job"],
            "previous_result": app.get("result"), "page_url": app.get("page_url"),
            "questions": store.questions(job_id=app["job_id"]), **context,
            "screenshot_path": str(work / (token + ".png"))}


def complete_application(store, job_id, token, result):
    uuid.UUID(token)
    validate(result, RESULT_SCHEMA)
    if result["status"] == "needs_answer" and not result["unanswered_questions"]:
        raise ValueError("needs_answer requires at least one question")
    if any(not q["question"].strip() for q in result["unanswered_questions"]):
        raise ValueError("Questions cannot be blank")
    screenshot = result.get("screenshot_path") or None
    if result["status"] == "review_ready" and not screenshot:
        raise ValueError("review_ready requires the current claim's screenshot")
    if screenshot:
        expected = work_directory(job_id) / (token + ".png")
        if Path(screenshot).resolve() != expected.resolve() or not expected.is_file() or not expected.stat().st_size:
            raise ValueError("Screenshot must be the current claim's screenshot_path and contain a saved image")
    if result["status"] == "review_ready" and (not result["page_url"] or not result.get("filled_fields")):
        raise ValueError("review_ready requires a page URL and the fields verified in the browser")
    with store.conn:
        store.conn.execute("BEGIN IMMEDIATE")
        app = store.get_application(job_id)
        if not app or app["engine"] != CODEX or app["status"] != "running" or app["session_id"] != token:
            raise ValueError("Claim is no longer active; result was not saved")
        store.conn.execute("UPDATE questions SET status='sent' WHERE job_id=? AND status='answered'", (job_id,))
        store.conn.execute("UPDATE questions SET status='superseded' WHERE job_id=? AND status='open'", (job_id,))
        for q in result["unanswered_questions"]:
            store.conn.execute("INSERT INTO questions (job_id, question, options, created_at) VALUES (?,?,?,?)",
                               (job_id, q["question"], json.dumps(q.get("options") or []), _iso()))
        store.conn.execute("UPDATE applications SET status=?, summary=?, page_url=?, screenshot=?, result_json=?, updated_at=? WHERE job_id=?",
                           (result["status"], result["summary"], result["page_url"], screenshot, json.dumps(result), _iso(), job_id))
    return {"job_id": job_id, "status": result["status"], "summary": result["summary"]}
