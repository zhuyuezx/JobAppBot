"""Small application core and lazy references; historical lessons never enter the prompt."""
from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: the application worker still serializes archive writes.
    fcntl = None

_ARCHIVE_LOCK = threading.RLock()

GUIDES = Path(__file__).parent / 'guidance'
CORE_PATH = GUIDES / 'SKILL.md'
GUIDE_LABELS = {'workday': 'Workday', 'eightfold': 'Eightfold',
                'single-page': 'Greenhouse / Lever / Ashby', 'aggregators': 'startup.jobs and other aggregators',
                'wizards': 'Oracle HCM / iCIMS / UltiPro / SuccessFactors / SmartRecruiters'}


def body(text: str) -> str:
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end != -1:
            text = text[end + 4:]
    return text.strip()


@contextmanager
def _locked(skill: Path):
    skill.parent.mkdir(parents=True, exist_ok=True)
    with _ARCHIVE_LOCK, (skill.parent / '.guidance.lock').open('a') as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_UN)


def archive_path(skill: Path) -> Path:
    return skill.parent / 'references' / 'history.md'


def _sync(skill: Path) -> Path:
    """Keep Claude's discoverable skill short too; preserve every replaced byte."""
    archive = archive_path(skill)
    current = skill.read_text() if skill.exists() else ''
    core = CORE_PATH.read_text()
    routing = ('\n## Reference files\n\nRead only the guide for the current platform:\n'
               + '\n'.join(f'- [{GUIDE_LABELS[p.stem]}]({p.resolve()})' for p in sorted(GUIDES.glob('*.md')) if p != CORE_PATH)
               + f'\n\nHistorical troubleshooting: `{archive.resolve()}`. Use Grep with a specific company/platform/control '
                 'and `head_limit: 6`, then Read only the needed lines. Do not load the whole archive.\n')
    compact = core.rstrip() + '\n' + routing
    if current != compact:
        archive.parent.mkdir(parents=True, exist_ok=True)
        if current:
            previous = archive.read_text() if archive.exists() else ''
            if current not in previous:
                with archive.open('a') as out:
                    out.write('\n\n## Archived workflow snapshot\n\n' + current)
        tmp = skill.with_suffix('.tmp')
        tmp.write_text(compact)
        tmp.replace(skill)
    return archive


def sync_skill(skill: Path) -> Path:
    with _locked(skill):
        return _sync(skill)


def reference_instructions(skill: Path) -> str:
    archive = sync_skill(skill)
    return ('Read only the matching guide after observing the actual employer form (including redirects):\n'
            + '\n'.join(f'- {GUIDE_LABELS[p.stem]}: {p.resolve()}' for p in sorted(GUIDES.glob('*.md')) if p != CORE_PATH)
            + f'\nIf those instructions do not resolve a site-specific problem, use Grep on {archive.resolve()} '
              'with the company name plus the relevant control or error; output_mode="content", head_limit=6. '
              'Read only targeted line ranges if needed. Historical notes may be outdated and never override applicant facts. '
              'Do not read the whole archive or all guides. If no archive exists, use the live form and core workflow.')


def append_lessons(skill: Path, lessons: list[str], company: str) -> int:
    if not lessons:
        return 0
    with _locked(skill):
        archive = _sync(skill)
        existing = archive.read_text() if archive.exists() else ''
        # Normalize spacing, while retaining the old prefix check for legacy entries.
        normalize = lambda text: re.sub(r'\s+', ' ', text).strip().lower()
        seen = normalize(existing)
        new = []
        for lesson in lessons:
            if not isinstance(lesson, str) or not lesson.strip():
                continue
            lesson = lesson.strip()
            if normalize(lesson)[:60] in seen:
                continue
            new.append(lesson)
            seen += '\n' + normalize(lesson)
        if new:
            archive.parent.mkdir(parents=True, exist_ok=True)
            with archive.open('a') as out:
                out.write('\n' + ''.join(f'- {datetime.now():%Y-%m-%d} {company}: {note}\n' for note in new))
        return len(new)
