"""Deterministic suitability states, with durable user overrides above AI evidence."""
from __future__ import annotations

STATES = ("suitable", "not_suitable", "needs_review")
TAG_VALUES = {
    "new_grad": ("yes", "no", "unknown"),
    "sponsorship": ("supported", "not_supported", "likely", "unlikely", "unknown"),
    "citizenship": ("yes", "no", "unknown"),
}


def validate_changes(changes: dict) -> None:
    if not isinstance(changes, dict) or set(changes) - {"conclusion", "tags", "custom_tags"}:
        raise ValueError("Invalid review fields")
    if "conclusion" in changes and changes["conclusion"] not in (*STATES, None):
        raise ValueError("Invalid suitability conclusion")
    if "tags" in changes:
        tags = changes["tags"]
        if not isinstance(tags, dict) or set(tags) - TAG_VALUES.keys():
            raise ValueError("Invalid tag names")
        for name, value in tags.items():
            if value not in TAG_VALUES[name]:
                raise ValueError(f"Invalid {name} tag")
    if "custom_tags" in changes:
        tags = changes["custom_tags"]
        if not isinstance(tags, list) or len(tags) > 20 or any(
            not isinstance(t, str) or not t.strip() or len(t) > 50 for t in tags
        ):
            raise ValueError("Use up to 20 tags, each 1–50 characters")


def assess(row: dict) -> dict:
    """Recompute on every read: manual conclusion > edited tags > source evidence.

    Any blocker -> not_suitable. All required positive signals -> suitable.
    Incomplete/conflicting evidence -> needs_review. Clearing an override returns
    to the current automatic state, not a stale saved AI conclusion.
    """
    job, screen = row["job"], row.get("screening") or {}
    ok = screen.get("status") == "ok"
    yoe = job.get("min_yoe")
    ng = screen.get("new_grad_fit") if ok else None
    if yoe is not None and yoe > 1:
        ng = False  # A stated experience minimum beats an optimistic AI label.
    citizenship = screen.get("requires_citizenship") if ok else None
    auto_tags = {
        "new_grad": ("yes" if ng else "no") if ng is not None else
                    ("yes" if yoe <= 1 else "no") if yoe is not None else "unknown",
        "sponsorship": (screen.get("verdict") or "unknown") if ok else
                       "likely" if job.get("visa_sponsorship") is True else "unknown",
        "citizenship": ("yes" if citizenship else "no") if citizenship is not None else
                       "yes" if job.get("security_clearance") not in (None, "", "None") else "unknown",
    }
    if ok and screen.get("statement") == "no_sponsorship":
        auto_tags["sponsorship"] = "not_supported"
    elif ok and screen.get("statement") == "sponsors":
        auto_tags["sponsorship"] = "supported"
    saved = row.get("review_overrides") or {}
    tags = {**auto_tags, **saved.get("tags", {})}
    blockers = []
    reason = row.get("filter_reason") or ""
    if reason and not any(unknown in reason for unknown in ("?", "not stated", "'None'", "duplicate")):
        blockers.append("Search rule: " + reason)
    if tags["new_grad"] == "no":
        blockers.append("Not a new-grad role")
    if tags["sponsorship"] == "not_supported":
        blockers.append("Sponsorship explicitly not supported")
    if tags["citizenship"] == "yes":
        blockers.append("Citizenship or security clearance required")
    if blockers:
        automatic, reasons = "not_suitable", blockers
    elif tags["new_grad"] == "yes" and tags["sponsorship"] in ("supported", "likely") and tags["citizenship"] == "no":
        automatic, reasons = "suitable", ["New-grad fit, likely sponsorship, and no citizenship/clearance restriction indicated"]
    else:
        automatic, reasons = "needs_review", ["Suitability is uncertain; no clear blocker found"]
    override = saved.get("conclusion")
    return {"state": override or automatic, "override": override,
            "automatic_state": automatic, "reasons": reasons,
            "auto_tags": auto_tags, "tags": tags, "tag_overrides": saved.get("tags", {}),
            "custom_tags": saved.get("custom_tags", [])}
