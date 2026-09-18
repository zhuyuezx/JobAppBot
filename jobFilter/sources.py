"""Job sources. Each fetcher returns normalized `Job`s tagged with `via`.

    hiringcafe   hiring.cafe search (the original source)
    simplify     SimplifyJobs/New-Grad-Positions listings.json (curated new-grad list)
    startupjobs  startup.jobs role listings (HTML)

Enabled/configured under the `sources` key of setup/search.json; see
setup/search.template.json. `fetch_all()` merges everything into one list;
cross-source dedup happens in the store (normalized apply URL, then
company+title).
"""
from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from curl_cffi import requests

from jobFilter.hiringcafe import HiringCafeClient
from jobFilter.models import Job

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache"

DEFAULT_SOURCES: dict[str, Any] = {
    "hiringcafe": {"enabled": True},
    "simplify": {"enabled": True, "categories": ["Software", "Software Engineering"], "max_age_days": 3},
    "startupjobs": {"enabled": True, "roles": ["software-engineer"], "max_pages": 10, "max_age_days": 2},
}

# ----- shared helpers ---------------------------------------------------------
_US_STATES = {"AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC"}
_COUNTRY_WORDS = {"US": ("united states", "usa", "u.s.", "remote in usa"), "CA": ("canada",), "GB": ("united kingdom", "uk", "england", "london"),
                  "DE": ("germany",), "IN": ("india",), "IE": ("ireland",), "AU": ("australia",), "SG": ("singapore",), "NL": ("netherlands",), "FR": ("france",), "BE": ("belgium",)}


def guess_countries(location: str, assume_us_for_remote: bool = False) -> list[str]:
    loc = (location or "").lower()
    found: list[str] = []
    for code, words in _COUNTRY_WORDS.items():
        if any(w in loc for w in words):
            found.append(code)
    if not found and re.search(r",\s*(" + "|".join(_US_STATES) + r")\b", location or ""):
        found.append("US")
    if not found and assume_us_for_remote and "remote" in loc:
        found.append("US")
    return found


def _job(via: str, **kw: Any) -> Job:
    base: dict[str, Any] = dict(company_website=None, location=None, workplace_type=None, countries=[], states=[], seniority=None,
                                min_yoe=None, visa_sponsorship=None, security_clearance=None, commitment=[], category=None,
                                requirements_summary=None, technical_tools=[], yearly_min_comp=None, yearly_max_comp=None,
                                published_at=None, published_millis=None, apply_url=None, source=None, listing_url=None, raw={})
    base.update(kw)
    return Job(via=via, **base)


# ----- hiring.cafe -----------------------------------------------------------------
def fetch_hiringcafe(search_state: dict[str, Any], max_pages: int = 25) -> list[Job]:
    client = HiringCafeClient()
    return [Job.from_hit(h) for h in client.search(search_state, max_pages=max_pages)]


# ----- SimplifyJobs -----------------------------------------------------------------
SIMPLIFY_URL = "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"
SIMPLIFY_CATEGORY = {"Software": "Software Development", "Software Engineering": "Software Development",
                     "AI/ML/Data": "Data and Analytics", "Hardware": "Engineering", "Quant": "Finance",
                     "Product": "Product Management", "Product Management": "Product Management"}


def _cached_get_json(url: str, cache_name: str) -> Any:
    """GET with ETag caching so a 14 MB file is not re-downloaded when unchanged."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    body, meta = CACHE_DIR / f"{cache_name}.json", CACHE_DIR / f"{cache_name}.etag"
    headers = {"If-None-Match": meta.read_text().strip()} if meta.exists() and body.exists() else {}
    r = requests.get(url, headers=headers, impersonate="chrome", timeout=120)
    if r.status_code == 304 and body.exists():
        return json.loads(body.read_text())
    r.raise_for_status()
    body.write_bytes(r.content)
    if r.headers.get("etag"):
        meta.write_text(r.headers["etag"])
    return r.json()


def fetch_simplify(cfg: dict[str, Any]) -> list[Job]:
    data = _cached_get_json(SIMPLIFY_URL, "simplify")
    cats = set(cfg.get("categories") or [])
    max_age = float(cfg.get("max_age_days", 3)) * 86400
    now = time.time()
    jobs: list[Job] = []
    for d in data:
        if not d.get("active") or not d.get("is_visible", True):
            continue
        if cats and d.get("category") not in cats:
            continue
        posted = d.get("date_posted") or d.get("date_updated") or 0
        if now - posted > max_age:
            continue
        locs = d.get("locations") or []
        location = "; ".join(locs) if locs else None
        countries = sorted({c for l in locs for c in guess_countries(l, assume_us_for_remote=True)})
        spons = d.get("sponsorship")
        jobs.append(_job("simplify",
            id=f"simplify___{d['id']}", title=d.get("title") or "", company=d.get("company_name") or "",
            location=location, countries=countries,
            states=sorted({m.group(1) for l in locs for m in [re.search(r",\s*([A-Z]{2})\b", l)] if m and m.group(1) in _US_STATES}),
            seniority="No Prior Experience Required", category=SIMPLIFY_CATEGORY.get(d.get("category") or "", d.get("category")),
            visa_sponsorship=True if spons == "Offers Sponsorship" else (False if spons in ("Does Not Offer Sponsorship", "U.S. Citizenship is Required") else None),
            security_clearance="Other" if spons == "U.S. Citizenship is Required" else None,
            requirements_summary=("Degrees: " + ", ".join(d["degrees"])) if d.get("degrees") else None,
            published_at=datetime.fromtimestamp(posted, tz=timezone.utc).isoformat(timespec="seconds"), published_millis=int(posted * 1000),
            apply_url=d.get("url"), listing_url="https://github.com/SimplifyJobs/New-Grad-Positions#-software-engineering-new-grad-roles",
            source=_ats_from_url(d.get("url") or ""), dedup_key=f"simplify___{d['id']}", raw=d))
    return jobs


def _ats_from_url(url: str) -> Optional[str]:
    u = url.lower()
    for key, name in (("myworkdayjobs", "workday"), ("greenhouse", "greenhouse"), ("lever.co", "lever"), ("ashbyhq", "ashby"), ("smartrecruiters", "smartrecruiters"),
                      ("icims", "icims"), ("workable", "workable"), ("oraclecloud", "oraclecloud"), ("eightfold", "eightfold"), ("successfactors", "successfactors"), ("ultipro", "ultipro"), ("jobvite", "jobvite"), ("bamboohr", "bamboohr")):
        if key in u:
            return name
    return None


# ----- startup.jobs -----------------------------------------------------------------
_SJ_CARD_SPLIT = re.compile(r'data-mark-visited-links-target="container"')
_SJ_TITLE = re.compile(r'data-post-template-target="title"[^>]*href="([^"]+)"[^>]*>.*?<div[^>]*>(.*?)</div>', re.S)
_SJ_COMPANY = re.compile(r'data-post-template-target="companyName"[^>]*>(.*?)</a>', re.S)
_SJ_LOCATION = re.compile(r'data-post-template-target="location"[^>]*>(.*?)</div>', re.S)
_SJ_TIME = re.compile(r'<time datetime="(\d{4}-\d{2}-\d{2}T[^"]+)"')


def _clean(fragment: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", fragment))).strip()


def fetch_startupjobs(cfg: dict[str, Any]) -> list[Job]:
    session = requests.Session(impersonate="chrome")
    max_age = float(cfg.get("max_age_days", 2)) * 86400
    now = datetime.now(timezone.utc)
    jobs: list[Job] = []
    for role in cfg.get("roles") or ["software-engineer"]:
        for page in range(1, int(cfg.get("max_pages", 10)) + 1):
            r = session.get(f"https://startup.jobs/roles/{role}?page={page}", timeout=60)
            if r.status_code != 200:
                break
            blocks = _SJ_CARD_SPLIT.split(r.text)[1:]
            if not blocks:
                break
            page_dates: list[datetime] = []
            for b in blocks:
                t, c, l, ts = _SJ_TITLE.search(b), _SJ_COMPANY.search(b), _SJ_LOCATION.search(b), _SJ_TIME.search(b)
                if not (t and ts):
                    continue
                posted = datetime.fromisoformat(ts.group(1).replace("Z", "+00:00"))
                page_dates.append(posted)
                if (now - posted).total_seconds() > max_age:
                    continue
                href, title = t.group(1), _clean(t.group(2))
                m = re.search(r"-(\d+)$", href)
                jid = f"startupjobs___{m.group(1) if m else href.strip('/')}"
                location = _clean(l.group(1)) if l else ""
                loc_links = " ".join(re.findall(r'href="(/locations/[^"]+)"', l.group(1))) if l else ""
                countries = guess_countries(location + " " + loc_links.replace("-", " "))
                jobs.append(_job("startupjobs",
                    id=jid, title=title, company=_clean(c.group(1)) if c else "", location=location or None, countries=countries,
                    states=[m2.group(1) for m2 in [re.search(r",\s*([A-Z]{2})\b", location)] if m2 and m2.group(1) in _US_STATES],
                    workplace_type="Remote" if "remote" in (location + b[:0]).lower() else None,
                    category="Software Development" if "engineer" in role or "developer" in role else None,
                    published_at=posted.isoformat(timespec="seconds"), published_millis=int(posted.timestamp() * 1000),
                    apply_url="https://startup.jobs" + href, listing_url="https://startup.jobs" + href,
                    source="startupjobs", dedup_key=jid, raw={"href": href, "role": role, "page": page}))
            # pages are roughly newest-first (page 1 mixes in featured posts); stop once a whole page is stale
            if page > 1 and page_dates and all((now - d).total_seconds() > max_age for d in page_dates):
                break
            time.sleep(0.5)
    return jobs


# ----- orchestration -----------------------------------------------------------------
def sources_config(cfg: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(DEFAULT_SOURCES))
    for name, val in (cfg.get("sources") or {}).items():
        if name.startswith("_"):
            continue
        if isinstance(val, bool):
            merged.setdefault(name, {})["enabled"] = val
        elif isinstance(val, dict):
            merged.setdefault(name, {}).update({k: v for k, v in val.items() if not k.startswith("_")})
    return merged


def fetch_all(cfg: dict[str, Any], search_state: Optional[dict[str, Any]] = None, max_pages: int = 25,
              log: Callable[[str], None] = lambda s: None) -> tuple[list[Job], dict[str, int], dict[str, str]]:
    """Fetch every enabled source. Returns (jobs, counts per source, errors per source)."""
    scfg = sources_config(cfg)
    jobs: list[Job] = []
    counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    runners = {
        "hiringcafe": lambda c: fetch_hiringcafe(search_state or cfg["search_state"], max_pages=max_pages),
        "simplify": fetch_simplify,
        "startupjobs": fetch_startupjobs,
    }
    for name, run in runners.items():
        c = scfg.get(name, {})
        if not c.get("enabled", False):
            continue
        try:
            got = run(c)
            counts[name] = len(got)
            jobs.extend(got)
            log(f"  {name}: {len(got)} jobs")
        except Exception as e:  # one broken source must not kill the scan
            errors[name] = f"{type(e).__name__}: {e}"
            log(f"  {name}: FAILED {errors[name]}")
    return jobs, counts, errors
