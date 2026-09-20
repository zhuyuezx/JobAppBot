"""Applicant profile, answer bank and resume access for the apply engine.

Files:
    setup/profile.json          structured facts, see setup/profile.template.json   (local, ignored)
    setup/answers.json          [{id, question, answer, updated}] reused across applications (local, ignored)
    setup/resume/*.pdf          the file to upload; newest is used                   (local, ignored)
    data/profile/resume.txt     cached text extraction of the resume                 (local)
    data/profile/settings.json  engine settings such as the model                    (local)
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from jobFilter.providers import CLAUDE, migrate_engine, validate_engine, validate_thinking_level

ROOT = Path(__file__).resolve().parent.parent
SETUP_DIR = ROOT / "setup"
PROFILE_DIR = ROOT / "data" / "profile"          # local caches and settings
PROFILE_PATH = SETUP_DIR / "profile.json"
ANSWERS_PATH = SETUP_DIR / "answers.json"
RESUME_TEXT_PATH = PROFILE_DIR / "resume.txt"
RESUME_DIR = SETUP_DIR / "resume"
SETTINGS_PATH = PROFILE_DIR / "settings.json"
DEFAULT_SETTINGS: dict[str, Any] = {"engine": CLAUDE, "model": "opus", "max_turns": 120,
                                  "codex_model": "", "codex_reasoning_effort": "", "codex_timeout": 900}
MODEL_CHOICES = ["opus", "sonnet", "haiku"]
TEMPLATE_PATH = SETUP_DIR / "profile.template.json"


def _strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


# ----- profile -------------------------------------------------------------
def load_profile() -> dict[str, Any]:
    if PROFILE_PATH.exists():
        return _strip(json.loads(PROFILE_PATH.read_text()))
    return _strip(json.loads(TEMPLATE_PATH.read_text()))


def profile_exists() -> bool:
    return PROFILE_PATH.exists()


def save_profile(profile: dict[str, Any]) -> None:
    SETUP_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n")


def init_profile(overwrite: bool = False) -> Path:
    if PROFILE_PATH.exists() and not overwrite:
        return PROFILE_PATH
    save_profile(_strip(json.loads(TEMPLATE_PATH.read_text())))
    return PROFILE_PATH


# ----- answer bank ---------------------------------------------------------
def load_answers() -> list[dict[str, Any]]:
    if ANSWERS_PATH.exists():
        return json.loads(ANSWERS_PATH.read_text())
    return []


def save_answers(answers: list[dict[str, Any]]) -> None:
    SETUP_DIR.mkdir(parents=True, exist_ok=True)
    ANSWERS_PATH.write_text(json.dumps(answers, indent=2, ensure_ascii=False) + "\n")


def _norm(q: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", q.lower()).strip()


def add_answer(question: str, answer: str) -> dict[str, Any]:
    """Insert or update by normalized question text."""
    answers = load_answers()
    key = _norm(question)
    for a in answers:
        if _norm(a["question"]) == key:
            a["answer"] = answer
            a["updated"] = int(time.time())
            save_answers(answers)
            return a
    entry = {"id": max([a.get("id", 0) for a in answers] + [0]) + 1, "question": question.strip(),
             "answer": answer, "updated": int(time.time())}
    answers.append(entry)
    save_answers(answers)
    return entry


def delete_answer(answer_id: int) -> bool:
    answers = load_answers()
    kept = [a for a in answers if a.get("id") != answer_id]
    if len(kept) == len(answers):
        return False
    save_answers(kept)
    return True


# ----- resume --------------------------------------------------------------
def resume_path() -> Optional[Path]:
    if not RESUME_DIR.exists():
        return None
    files = sorted(RESUME_DIR.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def resume_text(refresh: bool = False) -> str:
    path = resume_path()
    if not path:
        return ""
    if RESUME_TEXT_PATH.exists() and not refresh and RESUME_TEXT_PATH.stat().st_mtime >= path.stat().st_mtime:
        return RESUME_TEXT_PATH.read_text()
    try:
        import fitz  # PyMuPDF, optional
    except ImportError:
        return ""
    doc = fitz.open(path)
    text = reflow_resume("\n".join(page.get_text() for page in doc))
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    RESUME_TEXT_PATH.write_text(text)
    return text


_BULLET_RE = re.compile(r"^(?:[\uf09f\uf0b7\u2022\u25cf\u25aa\u2023\u2043\u00b7\u25e6\u2219]|[-*o](?=\s|$))\s*(.*)$")
_DATE_RE = re.compile(r"\b((Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}|\d{4})\s*[–—-]\s*((Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}|\d{4}|Present|Current|Expected.*)", re.I)


def reflow_resume(raw: str) -> str:
    """Turn PDF line-wrapped text into one line per bullet.

    PDF extraction yields the bullet glyph on its own line (or as a prefix)
    followed by the bullet's text broken at the column width. This joins
    those fragments back together so each bullet is a single `• ...` line,
    which is what application forms expect in a description box.
    """
    out: list[str] = []
    frags: list[str] = []
    in_bullet = False
    prev_len = 0

    def flush() -> None:
        nonlocal frags, in_bullet
        if in_bullet and frags:
            out.append("• " + " ".join(f.strip() for f in frags if f.strip()))
        frags, in_bullet = [], False

    for line in raw.splitlines():
        stripped = re.sub(r"[ \t]{2,}", "  ", line).strip()
        if not stripped:
            flush(); prev_len = 0
            continue
        m = _BULLET_RE.match(stripped)
        if m:
            flush()
            in_bullet = True
            frags = [m.group(1)] if m.group(1).strip() else []
            prev_len = len(m.group(1)) if frags else 999   # 999: next line is the first fragment
            continue
        is_header = bool(_DATE_RE.search(stripped))
        continues = in_bullet and not is_header and (prev_len >= 80 or stripped[:1].islower() or stripped[:1] in "(,;&")
        if continues:
            frags.append(stripped)
            prev_len = len(stripped)
            continue
        flush()
        out.append(stripped)
        prev_len = len(stripped)
    flush()
    return "\n".join(out)


# ----- engine settings -----------------------------------------------------
def load_settings() -> dict[str, Any]:
    data = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            data.update(json.loads(SETTINGS_PATH.read_text()))
        except ValueError:
            pass
    data["engine"] = migrate_engine(data["engine"])
    return data


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    data = load_settings()
    if "engine" in updates:
        data["engine"] = validate_engine(updates["engine"])
    if "model" in updates:
        data["model"] = str(updates["model"]).strip() or DEFAULT_SETTINGS["model"]
    if "max_turns" in updates:
        data["max_turns"] = max(10, min(400, int(updates["max_turns"])))
    if "codex_model" in updates:
        data["codex_model"] = str(updates["codex_model"]).strip()
    if "codex_reasoning_effort" in updates:
        data["codex_reasoning_effort"] = validate_thinking_level(updates["codex_reasoning_effort"])
    if "codex_timeout" in updates:
        data["codex_timeout"] = max(30, min(3600, int(updates["codex_timeout"])))
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2) + "\n")
    return data


def status() -> dict[str, Any]:
    p = resume_path()
    return {
        "profile_exists": profile_exists(),
        "answers": len(load_answers()),
        "resume": str(p) if p else None,
        "resume_text_chars": len(resume_text()) if p else 0,
    }
