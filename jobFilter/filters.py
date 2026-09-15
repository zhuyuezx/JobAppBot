"""Rule-based filtering on top of hiring.cafe's own search filters.

Each rule returns a rejection reason string, or None when the job passes.
Rules are configured under the `rules` key of setup/search.json.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from jobFilter.models import Job

Rule = Callable[[Job, dict[str, Any]], Optional[str]]


def _countries(job: Job, rules: dict) -> Optional[str]:
    req = rules.get("require_countries")
    if req and not set(job.countries) & set(req):
        return f"country {job.countries or '?'} not in {req}"
    return None


def _categories(job: Job, rules: dict) -> Optional[str]:
    req = rules.get("require_categories")
    if req and job.category not in req:
        return f"category '{job.category}'"
    return None


def _title_include(job: Job, rules: dict) -> Optional[str]:
    words = [w.lower() for w in rules.get("title_include") or []]
    if words and not any(w in job.title.lower() for w in words):
        return "title lacks include keyword"
    return None


def _title_exclude(job: Job, rules: dict) -> Optional[str]:
    t = job.title.lower()
    for w in rules.get("title_exclude") or []:
        if w.lower() in t:
            return f"title contains '{w}'"
    return None


def _max_yoe(job: Job, rules: dict) -> Optional[str]:
    limit = rules.get("max_min_yoe")
    if limit is not None and job.min_yoe is not None and job.min_yoe > limit:
        return f"requires {job.min_yoe} yoe > {limit}"
    return None


def _clearance(job: Job, rules: dict) -> Optional[str]:
    if rules.get("exclude_security_clearance") and job.security_clearance not in (None, "None"):
        return f"security clearance: {job.security_clearance}"
    return None


def _max_age(job: Job, rules: dict) -> Optional[str]:
    hours = rules.get("max_age_hours")
    if hours and job.published_millis:
        age_h = (time.time() * 1000 - job.published_millis) / 3_600_000
        if age_h > hours:
            return f"posted {age_h:.0f}h ago > {hours}h"
    return None


RULES: list[Rule] = [_countries, _categories, _title_include, _title_exclude, _max_yoe, _clearance, _max_age]


def apply_rules(jobs: list[Job], rules: dict[str, Any]) -> tuple[list[Job], list[tuple[Job, str]]]:
    """Split jobs into (kept, [(job, reason), ...]). Also dedups within the batch."""
    kept: list[Job] = []
    rejected: list[tuple[Job, str]] = []
    seen: set[str] = set()
    for job in jobs:
        if job.dedup_key in seen:
            rejected.append((job, "duplicate in batch"))
            continue
        seen.add(job.dedup_key)
        reason = next((r for rule in RULES if (r := rule(job, rules))), None)
        if reason:
            rejected.append((job, reason))
        else:
            kept.append(job)
    return kept, rejected
