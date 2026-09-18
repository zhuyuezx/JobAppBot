"""Post-fetch screening of new jobs with a small Claude model (headless Claude Code, subscription).

For every newly stored job:
  1. get the description (server-side where a JSON API exists; else Claude fetches the page),
  2. look up whether the employer sponsors visas: what the posting says + H-1B filing history
     found by web search (cached per company for `company_cache_days`),
  3. judge new-grad fit,
and store a structured verdict in the `screenings` table. Runs automatically at the end of
`jobFilter run`; `jobFilter screen` runs it by hand.

    claude -p "<task>" --model sonnet --allowedTools WebSearch WebFetch --json-schema <schema> --output-format json
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Optional

from curl_cffi import requests

from jobFilter.apply_engine import find_claude
from jobFilter.store import Store

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCREENING: dict[str, Any] = {
    "enabled": True,
    "model": "sonnet",
    "max_per_run": 40,        # cap per scan; the rest is picked up next hour
    "concurrency": 3,
    "company_cache_days": 30,
    "max_turns": 14,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "statement": {"type": "string", "enum": ["sponsors", "no_sponsorship", "not_mentioned"],
                      "description": "What the posting text itself says about visa sponsorship / work authorization."},
        "requires_citizenship": {"type": "boolean", "description": "Posting requires US citizenship, permanent residency, a security clearance, or ITAR/export-control eligibility."},
        "company_verdict": {"type": "string", "enum": ["likely", "unlikely", "unknown"],
                            "description": "Does this employer sponsor H-1B for entry-level software roles, judging from filing history and stated policy?"},
        "company_evidence": {"type": "array", "items": {"type": "string"}, "description": "Short facts with numbers/years, e.g. '412 H-1B LCAs in FY2025 (h1bdata.info)'."},
        "verdict": {"type": "string", "enum": ["likely", "unlikely", "unknown"],
                    "description": "Overall: will THIS job sponsor? no_sponsorship or requires_citizenship => unlikely; sponsors => likely; else the company verdict."},
        "new_grad_fit": {"type": "boolean", "description": "0-1 years of experience or a recent degree is enough to qualify."},
        "fit_score": {"type": "integer", "minimum": 0, "maximum": 10, "description": "Attractiveness for an F-1 new grad: unlikely sponsorship caps it at 2."},
        "summary": {"type": "string", "description": "One or two sentences a candidate can read in a list."},
        "evidence": {"type": "array", "items": {"type": "string"}, "description": "Verbatim quotes from the posting that support statement/requires_citizenship/new_grad_fit."},
        "sources": {"type": "array", "items": {"type": "string"}, "description": "URLs consulted."},
    },
    "required": ["statement", "requires_citizenship", "company_verdict", "company_evidence", "verdict", "new_grad_fit", "fit_score", "summary", "evidence", "sources"],
}


def screening_config(cfg: dict[str, Any]) -> dict[str, Any]:
    merged = dict(DEFAULT_SCREENING)
    merged.update({k: v for k, v in (cfg.get("screening") or {}).items() if not k.startswith("_")})
    return merged


def company_key(name: str) -> str:
    n = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|limited|plc|group|holdings|technologies|technology)\b\.?", " ", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


from jobFilter.descriptions import fetch_description  # noqa: E402,F401  (kept for callers)

# ----- the LLM call -----------------------------------------------------------------
def build_prompt(row: dict[str, Any], description: str, cached: Optional[dict[str, Any]]) -> str:
    job = row["job"]
    known = ""
    if cached:
        known = (f"\nKNOWN COMPANY SPONSORSHIP (cached, do not re-search): verdict={cached['verdict']}; evidence: "
                 + "; ".join(cached.get("evidence") or []) + "\n")
    desc = description.strip()
    desc_block = desc[:12000] if desc else "(not available; use WebFetch on the apply URL to read the posting)"
    return f"""Screen this job posting for a new graduate in the United States on an F-1 visa who will need H-1B sponsorship.

JOB
- Title: {job.get('title')}
- Company: {job.get('company')}
- Location: {job.get('location')}
- Apply URL: {job.get('apply_url')}
- Listed on: {job.get('via')}; ATS: {job.get('source')}
{known}
TASKS
1. Read the posting text below (fetch the apply URL with WebFetch if the text is missing or truncated). Decide `statement`: does it say it sponsors / will not sponsor (or requires current authorization without sponsorship) / says nothing. Set `requires_citizenship` if it needs US citizenship, permanent residency, a security clearance, or ITAR/export-control eligibility. Quote the exact sentences in `evidence`.
2. Company sponsorship history, unless KNOWN COMPANY SPONSORSHIP is given above: use WebSearch (2-4 searches, e.g. "<company> H-1B", "<company> h1bdata", "<company> visa sponsorship new grad") and WebFetch the most useful result (h1bdata.info, myvisajobs.com, h1bgrader.com, the company's careers FAQ). Record concrete facts with years and counts in `company_evidence` and the URLs in `sources`. Judge `company_verdict`: likely = files H-1B petitions regularly for software roles and no stated no-sponsorship policy; unlikely = few or no filings or a stated policy against sponsorship; unknown otherwise.
3. `new_grad_fit`: true if 0-1 years or a recent degree qualifies.
4. `verdict`: unlikely if statement is no_sponsorship or requires_citizenship; likely if statement is sponsors; otherwise the company verdict. `fit_score` 0-10 with unlikely capped at 2. `summary`: one or two plain sentences.

Be fast: do not browse beyond what is needed for these fields.

POSTING TEXT
{desc_block}
"""


def run_claude(prompt: str, model: str, max_turns: int) -> tuple[dict[str, Any], dict[str, Any]]:
    claude = find_claude()
    if not claude:
        raise RuntimeError("claude binary not found (set JOBFILTER_CLAUDE)")
    cmd = [claude, "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
           "--model", model, "--max-turns", str(max_turns), "--allowedTools", "WebSearch", "WebFetch",
           "--no-session-persistence"]
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    raw = proc.stdout.strip()
    try:
        evt = json.loads(raw[raw.index("{"):]) if raw else {}
    except ValueError:
        evt = {}
    out = evt.get("structured_output") or {}
    if not out and evt.get("result", "").strip().startswith("{"):
        try:
            out = json.loads(evt["result"])
        except ValueError:
            pass
    if not out:
        raise RuntimeError(f"no structured output (exit {proc.returncode}): {(evt.get('result') or proc.stderr or raw)[:300]}")
    return out, evt


# ----- orchestration -----------------------------------------------------------------
def screen_job(store: Store, row: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    job = row["job"]
    ckey = company_key(job.get("company") or "")
    cached = store.company_sponsorship(ckey, cfg["company_cache_days"]) if ckey else None
    description = fetch_description(row)
    t0 = time.time()
    try:
        out, evt = run_claude(build_prompt(row, description, cached), cfg["model"], cfg["max_turns"])
    except Exception as e:
        data = {"status": "failed", "summary": str(e)[:500], "model": cfg["model"]}
        store.save_screening(row["id"], data)
        return data
    data = {
        "status": "ok", "verdict": out.get("verdict"), "statement": out.get("statement"),
        "requires_citizenship": out.get("requires_citizenship"), "new_grad_fit": out.get("new_grad_fit"),
        "fit_score": out.get("fit_score"), "summary": out.get("summary"),
        "evidence": (out.get("evidence") or []) + [f"[company] {x}" for x in (out.get("company_evidence") or [])],
        "sources": out.get("sources") or [], "model": evt.get("model") or cfg["model"],
        "cost_usd": evt.get("total_cost_usd"), "seconds": round(time.time() - t0, 1),
        "description_chars": len(description),
    }
    store.save_screening(row["id"], data)
    if ckey and not cached and out.get("company_verdict"):
        store.save_company_sponsorship(ckey, job.get("company") or "", out["company_verdict"],
                                       out.get("company_evidence") or [], out.get("sources") or [])
    return data


def screen_batch(store: Store, rows: list[dict[str, Any]], cfg: dict[str, Any],
                 log: Callable[[str], None] = lambda s: None) -> dict[str, int]:
    """Screen rows with a small thread pool. Jobs of the same company are serialized so the
    first one fills the company cache and the rest reuse it."""
    counts = {"ok": 0, "failed": 0}
    by_company: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_company.setdefault(company_key(r["job"].get("company") or "") or r["id"], []).append(r)

    def run_company(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results = []
        for r in group:
            res = screen_job(store, r, cfg)
            results.append((r, res))
            log(f"  [{res.get('verdict') or 'FAILED':<8}] fit={res.get('fit_score', '-'):<2} {r['job'].get('company', '')[:22]:<24} {r['job'].get('title', '')[:50]}"
                + (f"  ({res.get('seconds')}s)" if res.get("seconds") else f"  {res.get('summary', '')[:80]}"))
        return results

    with ThreadPoolExecutor(max_workers=max(1, int(cfg.get("concurrency", 3)))) as pool:
        futures = [pool.submit(run_company, g) for g in by_company.values()]
        for f in as_completed(futures):
            for _, res in f.result():
                counts["ok" if res.get("status") == "ok" else "failed"] += 1
    return counts
