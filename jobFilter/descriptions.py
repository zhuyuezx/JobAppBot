"""Plain-text job descriptions without an LLM, where an ATS exposes JSON.

Used by the screening step and by the experience inference for sources that
carry no seniority metadata (startup.jobs).
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from curl_cffi import requests

from jobFilter.hiringcafe import HiringCafeClient, html_to_text


def _get_json(url: str, **kw) -> Any:
    r = requests.get(url, impersonate="chrome", timeout=60, headers={"accept": "application/json"}, **kw)
    r.raise_for_status()
    return r.json()


def fetch_description(row: dict[str, Any]) -> str:
    """Best-effort plain-text description; empty string when no server-side path exists."""
    job = row["job"]
    url = job.get("apply_url") or ""
    try:
        if job.get("via") == "hiringcafe":
            return HiringCafeClient().job_description_text(row["id"])
        m = re.match(r"https://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)/job/(.+?)(?:\?|$)", url, re.I)
        if m:  # Workday: same path under /wday/cxs/<tenant>/<site>/job/...
            tenant, wd, site, rest = m.groups()
            d = _get_json(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/job/{rest}")
            return html_to_text((d.get("jobPostingInfo") or {}).get("jobDescription") or "")
        m = re.search(r"greenhouse\.io/([^/?#]+)/jobs/(\d+)", url)
        if m:
            d = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
            return html_to_text(urllib.parse.unquote(d.get("content") or "").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))
        m = re.search(r"jobs\.lever\.co/([^/?#]+)/([0-9a-f-]{36})", url)
        if m:
            d = _get_json(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
            return html_to_text(d.get("description") or "") + "\n" + "\n".join(html_to_text(x.get("content") or "") for x in d.get("lists") or [])
        m = re.search(r"jobs\.smartrecruiters\.com/([^/?#]+)/(\d+)", url)
        if m:
            d = _get_json(f"https://api.smartrecruiters.com/v1/companies/{m.group(1)}/postings/{m.group(2)}")
            secs = (d.get("jobAd") or {}).get("sections") or {}
            return "\n\n".join(html_to_text(v.get("text") or "") for v in secs.values() if isinstance(v, dict))
        if job.get("via") == "startupjobs" and "startup.jobs/" in url:  # JobPosting JSON-LD on the listing page
            r = requests.get(url, impersonate="chrome", timeout=60)
            for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S):
                try:
                    items = json.loads(block)
                except ValueError:
                    continue
                for it in (items if isinstance(items, list) else [items]):
                    if isinstance(it, dict) and it.get("@type") == "JobPosting" and it.get("description"):
                        return html_to_text(it["description"])
            return ""
        m = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", url)
        if m:
            d = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}?includeCompensation=true")
            for jp in d.get("jobs") or []:
                if jp.get("id") == m.group(2):
                    return jp.get("descriptionPlain") or html_to_text(jp.get("descriptionHtml") or "")
    except Exception:
        return ""
    return ""




# ----- years-of-experience inference -----------------------------------------------
_YOE = re.compile(r"(\d{1,2})\s*(?:\+|\s*(?:-|–|to)\s*\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b[^.\n]{0,60}?\bexperience"
                  r"|experience[^.\n]{0,60}?\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.I)
_NEW_GRAD = re.compile(r"new\s*grad|recent(?:ly)?\s*graduat|entry[- ]level|early[- ]career|no (?:prior )?experience(?: required| necessary)?"
                       r"|0\s*(?:-|–|to)\s*[12]\s*(?:years?|yrs?)|university graduate|graduating (?:in|by|class of)|class of 20\d\d", re.I)


def infer_min_yoe(text: str) -> tuple[Any, bool]:
    """Return (min years of experience or None, new_grad_phrase_found).

    Takes the smallest number that appears next to the word "experience"; a
    new-grad phrase forces 0. None when the text states nothing.
    """
    if not text:
        return None, False
    ng = bool(_NEW_GRAD.search(text))
    years = [int(a or b) for a, b in _YOE.findall(text)]
    years = [y for y in years if y <= 20]
    if ng:
        return 0, True
    return (min(years) if years else None), False
