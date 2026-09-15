"""Apply engine: hands one job to Claude Code (headless, subscription login) with
the Claude in Chrome integration, so the form is filled in your own Chrome
window while this process streams what Claude is doing into a log.

    claude -p "<task>" --chrome --output-format stream-json --json-schema <result>

The task ends on the review page; Claude never clicks Submit. The result JSON
says whether it is review_ready or why it stopped (needs_answer, needs_login,
captcha, ...). Unanswered questions become rows in the `questions` table; once
you answer them the same session is resumed with `--resume <session_id>`.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from jobFilter import profile as prof
from jobFilter.store import Store

ROOT = Path(__file__).resolve().parent.parent
APPLY_DIR = ROOT / "data" / "apply"
# Model / turn cap come from data/profile/settings.json (UI: Applications tab);
# JOBFILTER_MODEL / JOBFILTER_MAX_TURNS env vars override them.

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["review_ready", "needs_answer", "needs_login", "captcha", "already_applied", "failed"]},
        "summary": {"type": "string", "description": "2-4 sentences: what was filled, what is left, any account created (email + password)."},
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
    },
    "required": ["status", "summary", "page_url", "unanswered_questions"],
}


# ----- environment ---------------------------------------------------------
def find_claude() -> Optional[str]:
    env = os.environ.get("JOBFILTER_CLAUDE")
    if env and Path(env).exists():
        return env
    which = shutil.which("claude")
    if which:
        return which
    candidates = glob.glob(str(Path.home() / ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude"))
    candidates += glob.glob(str(Path.home() / ".claude/local/claude"))
    candidates = [c for c in candidates if os.access(c, os.X_OK)]
    return sorted(candidates)[-1] if candidates else None


def chrome_host_installed() -> bool:
    paths = [
        Path.home() / "Library/Application Support/Google/Chrome/NativeMessagingHosts/com.anthropic.claude_code_browser_extension.json",
        Path.home() / ".config/google-chrome/NativeMessagingHosts/com.anthropic.claude_code_browser_extension.json",
    ]
    return any(p.exists() for p in paths)


def engine_status() -> dict[str, Any]:
    return {
        "claude": find_claude(),
        "chrome_host_installed": chrome_host_installed(),
        "settings": effective_settings(),
        "model_choices": prof.MODEL_CHOICES,
        "worker_alive": _worker is not None and _worker.is_alive(),
        "current_job": _worker.current_job if _worker else None,
        "profile": prof.status(),
    }


def effective_settings() -> dict[str, Any]:
    st = prof.load_settings()
    if os.environ.get("JOBFILTER_MODEL"):
        st["model"] = os.environ["JOBFILTER_MODEL"]
    if os.environ.get("JOBFILTER_MAX_TURNS"):
        st["max_turns"] = int(os.environ["JOBFILTER_MAX_TURNS"])
    return st


# ----- prompt --------------------------------------------------------------
def _safe_dir(job_id: str) -> Path:
    d = APPLY_DIR / re.sub(r"[^A-Za-z0-9_.-]+", "_", job_id)[:120]
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_prompt(app: dict[str, Any], work_dir: Path) -> str:
    job = app["job"]
    profile = prof.load_profile()
    answers = prof.load_answers()
    resume = prof.resume_path()
    resume_txt = prof.resume_text()
    rules = profile.pop("rules_for_claude", [])
    bank = "\n".join(f"- Q: {a['question']}\n  A: {a['answer']}" for a in answers) or "(empty)"
    return f"""You are filling a job application in the user's Chrome browser on their behalf.

JOB
- Title: {job.get('title')}
- Company: {job.get('company')}
- Apply URL: {job.get('apply_url') or app.get('hc_url')}
- Location: {job.get('location')}

TASK
1. Open the apply URL in a new tab. If it is a listing page, click the Apply button.
2. If the site requires an account: try signing in with the profile email first; if there is no account, create one with the profile email and a strong generated password, and put the password in the summary. If an email verification code is required, stop with status needs_login.
3. Fill every field of the application from the PROFILE, the RESUME and the ANSWER BANK below. Upload the resume file at: {resume or '(no resume file found)'}
4. Answer custom questions from the answer bank when a question means the same thing, otherwise from the profile and resume. For free-text questions (e.g. "why do you want to work here") write 2-4 concrete sentences grounded in the resume and the job.
5. If a required question cannot be answered truthfully from the material below, do NOT guess: leave it, continue with everything else, then stop with status needs_answer and list each question (with its options if it is a choice).
6. Continue through every step until you reach the final review/summary page. Take a screenshot of it and save it to disk at {work_dir / 'review.png'}. Then stop with status review_ready.
7. NEVER click the final Submit / Send / Apply-now button on the review page. The user will do that.
8. If a CAPTCHA, phone verification, or a login wall you cannot pass appears, stop with status captcha or needs_login and say where the tab is.
9. If the site says the user already applied, stop with status already_applied.
Leave the tab open at the end so the user can review.

RULES
{chr(10).join('- ' + r for r in rules)}

PROFILE (JSON)
{json.dumps(profile, indent=1, ensure_ascii=False)}

ANSWER BANK
{bank}

RESUME TEXT
{resume_txt[:6000]}
"""


def resume_message(questions: list[dict[str, Any]], work_dir: Path) -> str:
    lines = [f"- Q: {q['question']}\n  A: {q['answer']}" for q in questions]
    return ("The user answered the open questions:\n" + "\n".join(lines) +
            "\n\nGo back to the application tab, fill these answers in, continue to the review page, "
            f"save a screenshot of it to disk at {work_dir / 'review.png'}, and stop with status review_ready. "
            "Never click Submit.")


# ----- running -------------------------------------------------------------
def _log_event(evt: dict[str, Any], log) -> None:
    t = evt.get("type")
    if t == "assistant":
        for block in evt.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                log.write(f"[claude] {block['text'].strip()}\n")
            elif block.get("type") == "tool_use":
                inp = json.dumps(block.get("input", {}), ensure_ascii=False)
                log.write(f"[tool] {block.get('name')} {inp[:300]}\n")
    elif t == "result":
        log.write(f"[result] subtype={evt.get('subtype')} turns={evt.get('num_turns')} cost=${evt.get('total_cost_usd', 0):.3f}\n")
    elif t == "system" and evt.get("subtype") == "init":
        tools = [x for x in evt.get("tools", []) if "chrome" in x]
        log.write(f"[init] session={evt.get('session_id')} model={evt.get('model')} chrome_tools={len(tools)}\n")
    log.flush()


def run_application(store: Store, app: dict[str, Any]) -> dict[str, Any]:
    """Run (or resume) one application. Updates the store and returns the result dict."""
    job_id = app["job_id"]
    claude = find_claude()
    work_dir = _safe_dir(job_id)
    log_path = work_dir / "log.txt"
    store.update_application(job_id, status="running", log_path=str(log_path))
    store.bump_attempts(job_id)

    if not claude:
        store.update_application(job_id, status="failed", summary="claude binary not found (set JOBFILTER_CLAUDE)")
        return {"status": "failed"}

    answered = [q for q in store.questions(job_id=job_id, status="answered")]
    resuming = bool(app.get("session_id")) and bool(answered)
    prompt = resume_message(answered, work_dir) if resuming else build_prompt(app, work_dir)

    settings = effective_settings()
    cmd = [claude, "-p", prompt, "--chrome", "--output-format", "stream-json", "--verbose",
           "--json-schema", json.dumps(RESULT_SCHEMA), "--max-turns", str(settings["max_turns"]),
           "--model", settings["model"],
           "--allowedTools", "mcp__claude-in-chrome", "Read", "--add-dir", str(ROOT / "data")]
    if resuming:
        cmd += ["--resume", app["session_id"]]
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}

    result_evt: dict[str, Any] = {}
    session_id = app.get("session_id")
    with log_path.open("a") as log:
        log.write(f"\n===== {'resume' if resuming else 'start'} {time.strftime('%Y-%m-%d %H:%M:%S')} model={settings['model']} =====\n")
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except ValueError:
                log.write(line + "\n"); log.flush()
                continue
            if evt.get("session_id"):
                session_id = evt["session_id"]
            _log_event(evt, log)
            if evt.get("type") == "result":
                result_evt = evt
        proc.wait()

    out = result_evt.get("structured_output") or {}
    if not out:
        text = result_evt.get("result") or ""
        try:  # model may still have produced JSON text
            out = json.loads(text) if text.strip().startswith("{") else {}
        except ValueError:
            out = {}
    status = out.get("status") or ("failed" if (proc.returncode or result_evt.get("is_error")) else "failed")
    summary = out.get("summary") or (result_evt.get("result") or f"claude exited {proc.returncode}")[:2000]
    screenshot = out.get("screenshot_path") or (str(work_dir / "review.png") if (work_dir / "review.png").exists() else None)

    for q in answered:  # delivered
        store.conn.execute("UPDATE questions SET status = 'sent' WHERE id = ?", (q["id"],))
    for q in out.get("unanswered_questions") or []:
        if q.get("question"):
            store.add_question(job_id, q["question"], q.get("options"))
    if status == "needs_answer" and not out.get("unanswered_questions"):
        status = "failed"
    store.update_application(job_id, status=status, summary=summary, page_url=out.get("page_url"),
                             screenshot=screenshot, session_id=session_id, result=out or result_evt)
    store.conn.commit()
    return {"status": status, "summary": summary}


def requeue_if_answered(store: Store, job_id: str) -> bool:
    """After the user answers questions: queue the job again when nothing is open."""
    if store.questions(job_id=job_id, status="open"):
        return False
    store.update_application(job_id, status="queued")
    return True


# ----- background worker ----------------------------------------------------
class ApplyWorker(threading.Thread):
    def __init__(self, db_path: Path, poll: float = 3.0):
        super().__init__(daemon=True, name="apply-worker")
        self.db_path = db_path
        self.poll = poll
        self.current_job: Optional[str] = None
        self.stop_flag = threading.Event()

    def run(self) -> None:
        store = Store(self.db_path)
        while not self.stop_flag.is_set():
            app = store.next_queued()
            if not app:
                self.stop_flag.wait(self.poll)
                continue
            self.current_job = app["job_id"]
            try:
                run_application(store, app)
            except Exception as e:  # never kill the worker
                store.update_application(app["job_id"], status="failed", summary=f"engine error: {e}")
            finally:
                self.current_job = None


_worker: Optional[ApplyWorker] = None


def start_worker(db_path: Path) -> ApplyWorker:
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = ApplyWorker(db_path)
        _worker.start()
    return _worker
