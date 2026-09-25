"""Applicant profile, answer bank and resume access for the apply engine.

Files:
    setup/profile.json          structured facts, see setup/profile.template.json   (local, ignored)
    setup/answers.json          [{id, question, answer, updated}] reused across applications (local, ignored)
    setup/resume/*.pdf          resumes to upload, versioned by name (*_SDE_*, *_MLE_*) (local, ignored)
    setup/cover_letter/*.docx   cover letter templates, versioned the same way      (local, ignored)
    data/profile/resume*.txt    cached text extraction of each resume version        (local)
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
COVER_LETTER_DIR = SETUP_DIR / "cover_letter"
SETTINGS_PATH = PROFILE_DIR / "settings.json"
DEFAULT_SETTINGS: dict[str, Any] = {"engine": CLAUDE, "model": "opus", "max_turns": 120,
                                  "codex_model": "gpt-5.6-luna", "codex_reasoning_effort": "", "codex_timeout": 900,
                                  "resume": "auto"}
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
# One resume per job category, told apart by a version word in the file name
# (Resume_Jason_Zhu_SDE_2026-08-12.pdf, Resume_Jason_Zhu_MLE_2026-09-23.pdf).
# A PDF without one counts as the default (SDE) resume. A job uses the version
# whose rule its title matches; a version without a PDF of its own falls back
# to the default resume.
DEFAULT_RESUME = "sde"
RESUME_RULES = {
    "mle": re.compile(r"\b(machine[- ]learning|ml|mle|mlops|ai|genai|artificial intelligence|deep learning|llms?|nlp|"
                      r"natural language|computer vision|perception|agentic|data scien(ce|tist)|"
                      r"applied scien(ce|tist)|research (engineer|scientist))\b", re.I),
}
RESUME_VERSIONS = (DEFAULT_RESUME, *RESUME_RULES)
RESUME_CHOICES = ("auto", *RESUME_VERSIONS)
# "AI-native" / "AI-first" describe the company, not the role.
_AI_DESCRIPTOR = re.compile(r"\bAI[- ](native|first|powered|enabled|driven)\b", re.I)


def validate_resume_choice(choice: Any) -> str:
    choice = str(choice or "auto").strip().lower()
    if choice not in RESUME_CHOICES:
        raise ValueError(f"resume must be one of: {', '.join(RESUME_CHOICES)}")
    return choice


def job_resume(job: dict[str, Any], choice: Optional[str] = "auto") -> tuple[str, str]:
    """(version, reason): the version the user chose, else the first rule the job title matches."""
    choice = validate_resume_choice(choice)
    if choice != "auto":
        return choice, "chosen when the application was started"
    title = _AI_DESCRIPTOR.sub(" ", job.get("title") or "")
    for version, rule in RESUME_RULES.items():
        match = rule.search(title)
        if match:
            return version, f'title mentions "{match.group(0)}"'
    return DEFAULT_RESUME, "title matches no other resume version"


def file_version(path: Path) -> str:
    """Version named in the file name as a separate word (_MLE_, -mle, " MLE"), else the default."""
    words = re.split(r"[^a-z0-9]+", path.stem.lower())
    return next((v for v in RESUME_VERSIONS if v in words), DEFAULT_RESUME)


def _newest(folder: Path, pattern: str, version: str) -> Optional[Path]:
    files = [p for p in folder.glob(pattern) if not p.name.startswith("~$") and file_version(p) == version] \
        if folder.is_dir() else []   # ~$ files are Word's lock files for an open document
    return max(files, key=lambda p: p.stat().st_mtime, default=None)


def resume_path(version: Optional[str] = None) -> Optional[Path]:
    """Newest PDF of that resume version, falling back to the default resume."""
    return _newest(RESUME_DIR, "*.pdf", version or DEFAULT_RESUME) or _newest(RESUME_DIR, "*.pdf", DEFAULT_RESUME)


def cover_letter_template(version: Optional[str] = None) -> Optional[Path]:
    """Newest Word template of that version in setup/cover_letter/, falling back to the default one."""
    return (_newest(COVER_LETTER_DIR, "*.docx", version or DEFAULT_RESUME)
            or _newest(COVER_LETTER_DIR, "*.docx", DEFAULT_RESUME))


def application_resume(job: dict[str, Any], choice: Optional[str] = "auto") -> dict[str, Any]:
    """Resume version, file and text for one job's application."""
    version, reason = job_resume(job, choice)
    path = resume_path(version)
    if path and file_version(path) != version:
        reason += f"; setup/resume/ has no *_{version.upper()}_* PDF, so the default resume is used"
    return {"version": version, "reason": reason, "path": path, "text": resume_text(path=path) if path else ""}


def resume_text(refresh: bool = False, path: Optional[Path] = None) -> str:
    path = path or resume_path()
    if not path:
        return ""
    version = file_version(path)
    cache = RESUME_TEXT_PATH if version == DEFAULT_RESUME else RESUME_TEXT_PATH.with_name(f"resume.{version}.txt")
    if cache.exists() and not refresh and cache.stat().st_mtime >= path.stat().st_mtime:
        return cache.read_text()
    try:
        import fitz  # PyMuPDF, optional
    except ImportError:
        return ""
    doc = fitz.open(path)
    text = reflow_resume("\n".join(page.get_text() for page in doc))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text)
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
    lines = raw.splitlines()

    def flush() -> None:
        nonlocal frags, in_bullet
        if in_bullet and frags:
            text = ""
            for f in (f.strip() for f in frags if f.strip()):
                # A line broken after a hyphen ("hot-" / "swaps") joins without a space.
                text += f if re.search(r"\w-$", text) else (" " + f if text else f)
            out.append("• " + text)
        frags, in_bullet = [], False

    def dates_follow(i: int) -> bool:
        """The next non-blank line is only a date range, as LaTeX PDFs put it under an entry title."""
        nxt = next((l.strip() for l in lines[i + 1:] if l.strip()), "")
        m = _DATE_RE.match(nxt)
        return bool(m) and len(nxt) - m.end() <= 12   # room for " (Expected)"

    for i, line in enumerate(lines):
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
        is_header = bool(_DATE_RE.search(stripped)) or dates_follow(i)
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


def application_settings(updates: dict[str, Any]) -> dict[str, Any]:
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
    if "resume" in updates:
        data["resume"] = validate_resume_choice(updates["resume"])
    return data


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    data = application_settings(updates)
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
        # Versions with their own PDF; the others fall back to the default resume.
        "resume_versions": {v: str(f) if (f := _newest(RESUME_DIR, "*.pdf", v)) else None for v in RESUME_RULES},
        "cover_letter_templates": {v: str(f) if (f := _newest(COVER_LETTER_DIR, "*.docx", v)) else None
                                   for v in RESUME_VERSIONS},
    }
