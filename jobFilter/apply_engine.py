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
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from jobFilter import cover_letter, profile as prof
from jobFilter.store import ACTIVE_STATUSES, Store
from jobFilter.application_state import RESULT_SCHEMA, structured_result, work_directory
from jobFilter.providers import CLAUDE, CODEX, validate_engine

ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = ROOT / ".claude" / "skills" / "apply-job" / "SKILL.md"
LESSONS_HEADER = "## Learned from runs"


def load_skill() -> str:
    """Body of the apply-job skill (front matter stripped)."""
    if not SKILL_PATH.exists():
        return ""
    text = SKILL_PATH.read_text()
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    return text.strip()


def append_lessons(lessons: list[str], company: str) -> int:
    """Append new lessons to the skill's 'Learned from runs' section. Returns how many were added."""
    if not lessons or not SKILL_PATH.exists():
        return 0
    text = SKILL_PATH.read_text()
    existing = text.lower()
    new = [l.strip() for l in lessons if l and l.strip() and l.strip().lower()[:60] not in existing]
    if not new:
        return 0
    if LESSONS_HEADER not in text:
        text = text.rstrip() + f"\n\n{LESSONS_HEADER}\n\n"
    date = time.strftime("%Y-%m-%d")
    text = text.rstrip() + "\n" + "".join(f"- {date} {company}: {l}\n" for l in new)
    SKILL_PATH.write_text(text)
    return len(new)
# Model / turn cap come from data/profile/settings.json (UI: Applications tab);
# JOBFILTER_MODEL / JOBFILTER_MAX_TURNS env vars override them.

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
    from jobFilter import bridge
    from jobFilter.codex import find_codex, model_choices
    return {
        "bridge": bridge.status(),
        "codex": find_codex(),
        "codex_models": model_choices(),
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

def build_prompt(app: dict[str, Any], work_dir: Path, letter: Optional[dict[str, Any]] = None) -> str:
    job = app["job"]
    letter = letter or {}
    profile = prof.load_profile()
    answers = prof.load_answers()
    resume = prof.application_resume(job, (app.get("settings") or {}).get("resume"))
    rules = profile.pop("rules_for_claude", [])
    bank = "\n".join(f"- Q: {a['question']}\n  A: {a['answer']}" for a in answers) or "(empty)"
    skill = load_skill()
    return f"""Fill the job application below in the user's Chrome. Follow the SKILL exactly; it is the accumulated experience from previous applications.

JOB
- Title: {job.get('title')}
- Company: {job.get('company')}
- Apply URL: {job.get('apply_url') or app.get('hc_url')}
- Location: {job.get('location')}
- Resume file to upload: {resume['path'] or '(no resume file found)'}
- Resume version: {resume['version'].upper()} ({resume['reason']})
- Cover letter file to upload: {cover_letter.task_line(letter)}

At the end, return the structured result. In `lessons`, list only new reusable facts about this site's form that the SKILL does not already say (or an empty list). In `screenshot_path`, give the path returned by the screenshot tool; do not copy or convert files.

===== SKILL =====
{skill}

===== USER RULES =====
{chr(10).join('- ' + r for r in rules)}

===== PROFILE (JSON) =====
{json.dumps(profile, indent=1, ensure_ascii=False)}

===== ANSWER BANK =====
{bank}

===== RESUME TEXT =====
{resume['text'][:6000]}

===== COVER LETTER TEXT =====
{letter.get('text') or '(none)'}
"""


def resume_message(questions: list[dict[str, Any]], work_dir: Path, after: Optional[str] = None,
                   letter: Optional[dict[str, Any]] = None) -> str:
    parts = []
    if after == "needs_cover_letter" and (letter or {}).get("status") == "ok":
        parts.append(f"JobAppBot wrote the cover letter for this job: {letter['path']}\n"
                     "Find the application tab (tabs_context_mcp) and re-read the page. Upload this file to the cover letter "
                     "field you stopped at, or paste the text below into a cover letter text box (keep its blank lines). "
                     "Check that the field shows it and list it in filled_fields.\n\n"
                     f"===== COVER LETTER TEXT =====\n{letter.get('text', '')}")
    elif after == "needs_cover_letter":
        parts.append(f"No cover letter could be written ({(letter or {}).get('reason') or 'unknown reason'}). Find the application "
                     "tab and continue: leave an optional cover letter field empty; if it is required, fill everything else "
                     "and say in the summary that the cover letter is missing. Do not stop for a cover letter again.")
    if after in ("needs_login", "captcha"):
        parts.append("The user has finished the step you stopped at (account creation / sign-in / verification code / CAPTCHA) "
                     "in the application tab. Find that tab (tabs_context_mcp), take a screenshot, re-read the page, and continue "
                     "from its current state. Do not open the apply URL again and do not touch password fields.")
    if questions:
        lines = [f"- Q: {q['question']}\n  A: {q['answer']}" for q in questions]
        parts.append("The user answered the open questions:\n" + "\n".join(lines) + "\n\nFill these answers in.")
    parts.append("Continue to the review page, take a screenshot with save_to_disk and report its path, and stop with status "
                 "review_ready. Never click Submit. Include any new reusable facts about this form in `lessons`.")
    return "\n\n".join(parts)


# ----- running -------------------------------------------------------------
# Claude processes of running applications, so the UI can stop one.
_processes: dict[str, subprocess.Popen] = {}
_processes_lock = threading.Lock()
STOP_SUMMARY = "Stopped by you. Run again to retry, or delete the application."
COVER_LETTER_SUMMARY = "The form has a cover letter field: writing a cover letter for this job, then continuing."


def stop_application(store: Store, job_id: str, status: str = "failed", summary: str = STOP_SUMMARY) -> dict[str, Any]:
    """End a queued or running attempt: record `status`, then end its process.

    A Claude run is terminated here. A Codex run sees that its claim is no longer
    running and stops its own process. Either way the run's late result is dropped.
    Also clears a run left 'running' by a restart, where no process exists.
    """
    app = store.get_application(job_id)
    if not app:
        raise ValueError("Unknown application")
    if app["status"] not in ACTIVE_STATUSES:
        raise ValueError("Only a queued or running application can be stopped.")
    store.update_application(job_id, status=status, summary=summary)
    if app.get("log_path") and Path(app["log_path"]).parent.exists():
        with Path(app["log_path"]).open("a") as log:
            log.write(f"[stopped] {time.strftime('%Y-%m-%d %H:%M:%S')} {summary}\n")
    with _processes_lock:
        proc = _processes.get(job_id)
    if proc and proc.poll() is None:
        proc.terminate()
        threading.Timer(5, lambda: proc.poll() is None and proc.kill()).start()
    return store.get_application(job_id)


def _still_running(store: Store, job_id: str) -> bool:
    current = store.get_application(job_id)
    return bool(current) and current["status"] == "running"


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
    if app.get("engine") == CODEX:
        from jobFilter.bridge_engine import run_application as run_bridge
        return run_bridge(store, app)
    validate_engine(app.get("engine", CLAUDE))
    job_id = app["job_id"]
    claude = find_claude()
    work_dir = work_directory(job_id)
    log_path = work_dir / "log.txt"
    store.update_application(job_id, status="running", log_path=str(log_path))
    store.bump_attempts(job_id)

    if not claude:
        store.update_application(job_id, status="failed", summary="claude binary not found (set JOBFILTER_CLAUDE)")
        return {"status": "failed"}

    answered = store.questions(job_id=job_id, status="answered")
    last_status = (app.get("result") or {}).get("status")
    # Resume the same Claude session when the user answered questions or finished a login/CAPTCHA handoff;
    # otherwise (first run, or a retry after a hard failure) start fresh.
    resuming = bool(app.get("session_id")) and (bool(answered) or last_status in ("needs_login", "captcha", "needs_cover_letter"))
    settings = app.get("settings") or effective_settings()
    with log_path.open("a") as log:
        def note(line: str) -> None:
            log.write(line + "\n"); log.flush()
        # The letter is written only once the form turns out to have a cover letter field.
        if resuming:
            letter = cover_letter.prepare(app, settings, log=note) if last_status == "needs_cover_letter" else None
            prompt = resume_message(answered, work_dir, after=last_status, letter=letter)
            letter_given = letter is not None or cover_letter.load(job_id).get("status") in ("ok", "failed")
        else:
            letter = cover_letter.prepare(app, settings, log=note, write=False)
            prompt = build_prompt(app, work_dir, letter)
            letter_given = letter["status"] != "pending"
    if not _still_running(store, job_id):   # stopped or deleted while the letter was written
        return {"status": "stopped", "summary": STOP_SUMMARY}

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
        with _processes_lock:
            _processes[job_id] = proc
        try:
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
        finally:
            with _processes_lock:
                _processes.pop(job_id, None)
    if not _still_running(store, job_id):   # stopped or deleted: keep what the user recorded
        return {"status": "stopped", "summary": STOP_SUMMARY}

    out = structured_result(result_evt)
    status = out.get("status") or "failed"
    summary = out.get("summary") or (result_evt.get("result") or f"claude exited {proc.returncode}")[:2000]
    screenshot = _store_screenshot(out.get("screenshot_path"), work_dir)
    added = append_lessons(out.get("lessons") or [], app["job"].get("company") or job_id)
    if added:
        with log_path.open("a") as log:
            log.write(f"[skill] {added} new lesson(s) appended to {SKILL_PATH.relative_to(ROOT)}\n")

    for q in answered:  # delivered
        store.conn.execute("UPDATE questions SET status = 'sent' WHERE id = ?", (q["id"],))
    for q in out.get("unanswered_questions") or []:
        if q.get("question"):
            store.add_question(job_id, q["question"], q.get("options"))
    if status == "needs_answer" and not out.get("unanswered_questions"):
        status = "failed"
    if status == "needs_cover_letter":
        status, summary = ("failed", "Stopped for a cover letter again after being given an answer. " + summary) if letter_given \
            else ("queued", COVER_LETTER_SUMMARY)
    store.update_application(job_id, status=status, summary=summary, page_url=out.get("page_url"),
                             screenshot=screenshot, session_id=session_id, result=out or result_evt)
    store.conn.commit()
    if status == "queued":   # write the letter and continue at once, while the tab is still on that field
        current = store.get_application(job_id)
        if current and current["status"] == "queued":
            return run_application(store, current)
    return {"status": status, "summary": summary}


def _store_screenshot(reported: Optional[str], work_dir: Path) -> Optional[str]:
    """Copy the screenshot Claude reported (usually a temp jpg) into the job folder."""
    candidates = [Path(reported)] if reported else []
    candidates += [work_dir / "review.png", work_dir / "review.jpg"]
    for src in candidates:
        if src.exists():
            if src.parent == work_dir:
                return str(src)
            dst = work_dir / ("review" + (src.suffix.lower() or ".png"))
            shutil.copyfile(src, dst)
            return str(dst)
    return None


def requeue_if_answered(store: Store, job_id: str) -> bool:
    """After the user answers questions: queue the job again when nothing is open."""
    app = store.get_application(job_id)
    if not app or app["status"] != "needs_answer":
        return False
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
        try:
            while not self.stop_flag.is_set():
                app = store.next_queued()
                if not app:
                    self.stop_flag.wait(self.poll)
                    continue
                self.current_job = app["job_id"]
                try:
                    result = run_application(store, app)
                    if result["status"] == "queued":
                        self.stop_flag.wait(self.poll)
                except Exception as e:  # never kill the worker
                    store.update_application(app["job_id"], status="failed", summary=f"engine error: {e}")
                finally:
                    self.current_job = None
        finally:
            store.close()


_worker: Optional[ApplyWorker] = None


def start_worker(db_path: Path) -> ApplyWorker:
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = ApplyWorker(db_path)
        _worker.start()
    return _worker
