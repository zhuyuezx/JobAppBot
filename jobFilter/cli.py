"""Commands:

    python -m jobFilter run [URL] [--no-store] [--xlsx PATH]   fetch -> rules -> store -> print list -> daily excel
    python -m jobFilter validate                               check setup/search.json against hiring.cafe's schema
    python -m jobFilter list [--since 24 | --date YYYY-MM-DD]  read back from the local db
    python -m jobFilter excel [--date YYYY-MM-DD | --since 24] [--out PATH]
    python -m jobFilter serve [--port 8765]                    local web UI
    python -m jobFilter schedule [--interval 3600]             foreground loop: run every N seconds
"""
from __future__ import annotations
from jobFilter.providers import APPLICATION_ENGINES


import argparse
import json
import sys
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobFilter import schema
from jobFilter.filters import apply_rules
from jobFilter.models import Job

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "setup" / "search.json"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "jobs.db"
EXCEL_DIR = DATA_DIR / "excel"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def load_config(path: Path) -> dict[str, Any]:
    cfg = json.loads(path.read_text())
    cfg["search_state"] = schema.strip_comments(cfg.get("search_state", {}))
    cfg["rules"] = schema.strip_comments(cfg.get("rules", {}))
    return cfg


def search_state_from_url(url: str) -> dict[str, Any]:
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    if "searchState" not in qs:
        raise ValueError("URL has no searchState parameter")
    return json.loads(qs["searchState"][0])


def format_line(j: Job) -> str:
    posted = (j.published_at or "")[:16].replace("T", " ")
    visa = "visa:yes" if j.visa_sponsorship else "visa:?"
    via = "" if j.via == "hiringcafe" else f" | via {j.via}"
    return f"{posted}  {j.title} | {j.company} | {j.location or '?'} | {visa}{via}\n    {j.apply_url or j.hc_url}"


def today_local() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ----- commands -----------------------------------------------------------
def cmd_validate(args) -> int:
    cfg = load_config(args.config)
    errors, warnings = schema.validate(cfg["search_state"])
    for w in warnings:
        log(f"warning: {w}")
    for e in errors:
        log(f"error: {e}")
    log("search_state OK" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    search_state = search_state_from_url(args.url) if args.url else cfg["search_state"]
    errors, warnings = schema.validate(search_state)
    for w in warnings:
        log(f"warning: {w}")
    if errors:
        for e in errors:
            log(f"error: {e}")
        return 1
    rules = {} if args.no_rules else cfg["rules"]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    from jobFilter import sources
    if args.url:  # a pasted hiring.cafe URL means "run just that search"
        cfg["sources"] = {"hiringcafe": True, "simplify": False, "startupjobs": False, "applyguy": False}
    log(f"[{run_id}] fetching from {', '.join(n for n, c in sources.sources_config(cfg).items() if c.get('enabled'))}")
    jobs, counts, errors = sources.fetch_all(cfg, search_state=search_state, max_pages=args.max_pages, log=log)
    if not jobs and errors:
        log("every source failed")
        return 2

    kept, rejected = apply_rules(jobs, rules)
    if rules:  # sources without experience metadata: read the posting and re-check the experience rule
        from jobFilter.descriptions import fetch_description
        from jobFilter.filters import enrich_experience
        kept, more = enrich_experience(kept, rules, fetch_description, log=log)
        rejected += more
    log(f"{len(jobs)} fetched, {len(kept)} kept, {len(rejected)} rejected")
    for reason, n in Counter(r for _, r in rejected).most_common():
        log(f"  - {reason}: {n}")
    kept.sort(key=lambda j: j.published_millis or 0, reverse=True)

    to_print = kept
    if not args.no_store:
        from jobFilter.store import Store
        store = Store(DB_PATH)
        excluded = [(job, reason) for job, reason in rejected if reason != "duplicate in batch"]
        new = store.upsert_many(kept + [job for job, _ in excluded], run_id,
                                rejection_reasons={job.id: reason for job, reason in excluded})
        store.record_run(run_id, search_state, len(jobs), len(kept), len(new))
        log(f"{len(new)} new (db now {store.count()} jobs); rule-excluded postings are saved with reasons")
        if args.new_only:
            kept_ids = {job.id for job in kept}
            to_print = [job for job in new if job.id in kept_ids]
        # Post-fetch screening of the new jobs (sponsorship + fit) with a small model.
        from jobFilter import screen
        scfg = screen.screening_config(cfg)
        if scfg.get("enabled") and not args.no_screen:
            todo = store.unscreened(limit=int(scfg.get("max_per_run", 40)))
            if todo:
                log(f"screening {len(todo)} unscreened job(s) with {screen.model_label(scfg)} (cap {scfg.get('max_per_run')}/run)")
                counts = screen.screen_batch(store, todo, scfg, log=log)
                log(f"screened: {counts}")
        # Daily collection: regenerate today's workbook from everything first seen today.
        from jobFilter.excel import write_excel
        daily = write_excel(store.query(date=today_local()), EXCEL_DIR / f"{today_local()}.xlsx", today_local())
        log(f"daily excel: {daily}")
        store.close()

    for j in to_print:
        print(format_line(j))

    if args.xlsx:
        from jobFilter.excel import write_excel
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = [{"first_seen": now, "hc_url": j.hc_url, "job": j.to_dict()} for j in to_print]
        log(f"excel: {write_excel(rows, args.xlsx, run_id)}")
    return 0


def _select_rows(store, args):
    if args.since is not None:
        return store.query(since_hours=args.since)
    if args.date:
        return store.query(date=args.date)
    return store.query()


def cmd_list(args) -> int:
    from jobFilter.store import Store
    store = Store(DB_PATH)
    rows = _select_rows(store, args)
    if args.source:
        rows = [r for r in rows if (r["job"].get("via") or "hiringcafe") == args.source]
    # day found desc, posting day desc; within a day keep a company's postings together
    rows.sort(key=lambda r: (r["first_seen"][:10], (r["job"].get("published_at") or "")[:10]), reverse=True)
    from itertools import groupby
    ordered = []
    for _, grp in groupby(rows, key=lambda r: (r["first_seen"][:10], (r["job"].get("published_at") or "")[:10])):
        ordered += sorted(grp, key=lambda r: ((r["job"].get("company") or "").lower(), r["job"].get("title") or "", r["job"].get("location") or ""))
    rows = ordered
    by_via: dict[str, list] = {}
    for r in rows:
        by_via.setdefault(r["job"].get("via") or "hiringcafe", []).append(r)
    for via in sorted(by_via, key=lambda v: ["hiringcafe", "simplify", "startupjobs", "applyguy"].index(v) if v in ("hiringcafe", "simplify", "startupjobs", "applyguy") else 9):
        print(f"\n===== {via} ({len(by_via[via])}) =====")
        for r in by_via[via]:
            j = r["job"]
            print(f"found {r['first_seen'][:16].replace('T',' ')}  posted {(j.get('published_at') or '?')[:10]}  {j['title']} | {j['company']} | {j.get('location') or '?'}\n    {j.get('apply_url') or r['hc_url']}")
    log(f"{len(rows)} jobs")
    return 0


def cmd_excel(args) -> int:
    from jobFilter.excel import write_excel
    from jobFilter.store import Store
    store = Store(DB_PATH)
    rows = _select_rows(store, args)
    label = args.date or (f"last{int(args.since)}h" if args.since is not None else "all")
    out = args.out or EXCEL_DIR / f"{label}.xlsx"
    log(f"{len(rows)} rows -> {write_excel(rows, out, label)}")
    return 0


def cmd_serve(args) -> int:
    from jobFilter.server import serve
    serve(DB_PATH, host=args.host, port=args.port)
    return 0


def cmd_schedule(args) -> int:
    from jobFilter.scheduler import run_forever
    run_forever(interval=args.interval, config=args.config, run_immediately=not args.wait)
    return 0


def cmd_screen(args) -> int:
    from jobFilter import screen
    from jobFilter.store import Store
    cfg = load_config(args.config)
    scfg = screen.screening_config(cfg)
    if args.provider:
        scfg["provider"] = args.provider
    if args.model:
        scfg["codex_model" if scfg["provider"] == "codex" else "model"] = args.model
    store = Store(DB_PATH)
    if args.job:
        rows = [r for r in store.query() if r["id"].startswith(args.job)]
        if len(rows) != 1:
            log(f"{len(rows)} jobs match '{args.job}'"); return 1
    else:
        rows = store.unscreened(limit=args.limit, since_hours=args.since)
    if not rows:
        log("nothing to screen"); return 0
    log(f"screening {len(rows)} job(s) with {screen.model_label(scfg)}")
    counts = screen.screen_batch(store, rows, scfg, log=log)
    log(f"done: {counts}")
    return 1 if counts["failed"] or counts["skipped"] else 0


def cmd_profile(args) -> int:
    from jobFilter import profile as prof
    if args.action == "init":
        path = prof.init_profile(overwrite=args.force)
        prof.resume_text(refresh=True)
        prof.role_descriptions(refresh=True)
        log(f"profile: {path}\nresume: {prof.resume_path()}\nedit the JSON or use the Profile tab in the UI")
    else:
        print(json.dumps(prof.status(), indent=2))
    return 0


def cmd_apply(args) -> int:
    from jobFilter import apply_engine
    from jobFilter.store import Store
    store = Store(args.db or DB_PATH)
    if args.action == "queue":
        if not args.job_id:
            log("queue requires a job_id prefix"); return 1
        matches = [r for r in store.query() if r["id"].startswith(args.job_id)]
        if len(matches) != 1:
            log(f"{len(matches)} jobs match '{args.job_id}'; give a longer id prefix")
            return 1
        settings = apply_engine.effective_settings()
        a = store.queue_application(matches[0]["id"], args.engine or settings["engine"], settings)
        log(f"queued: {a['job']['title']} @ {a['job']['company']}")
        return 0
    if args.action == "list":
        for a in store.list_applications():
            print(f"{a['status']:<15} {a['job']['title']} @ {a['job']['company']}  [{a['job_id'][:40]}]\n    {a.get('summary') or ''}")
        return 0
    if args.action == "worker":
        log(f"engine: {json.dumps(apply_engine.engine_status())}")
        app = store.next_queued()
        while app:
            log(f"running {app['job']['title']} @ {app['job']['company']}")
            res = apply_engine.run_application(store, app)
            log(f"  -> {res['status']}: {res['summary'][:200]}")
            if res["status"] == "queued":
                log("Another bridge application is active; try again after it finishes."); return 1
            app = None if args.once else store.next_queued()
        log("no queued applications")
        return 0
    return 1


# ----- parser -------------------------------------------------------------
SUBCOMMANDS = {"run", "validate", "list", "excel", "serve", "schedule", "apply", "profile", "screen"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobFilter", description="hiring.cafe fetch -> filter -> collect")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="fetch from all enabled sources, filter, store, print list, write daily excel")
    r.add_argument("url", nargs="?", help="hiring.cafe search URL: run only that search (default: config search_state + all sources)")
    r.add_argument("--no-rules", action="store_true", help="skip the rules block")
    r.add_argument("--no-store", action="store_true", help="print only, don't touch the db or daily excel")
    r.add_argument("--new-only", action="store_true", help="print only jobs not seen in earlier runs")
    r.add_argument("--xlsx", type=Path, help="also write this run's list to an .xlsx file")
    r.add_argument("--max-pages", type=int, default=25)
    r.add_argument("--no-screen", action="store_true", help="skip the post-fetch LLM screening")
    r.set_defaults(func=cmd_run)

    sc2 = sub.add_parser("screen", help="LLM-screen jobs for sponsorship / fit (default: unscreened ones)")
    sc2.add_argument("--limit", type=int, default=40)
    sc2.add_argument("--since", type=float, metavar="HOURS", help="only jobs first seen within the last N hours")
    sc2.add_argument("--job", help="job id prefix: (re)screen this one job")
    sc2.add_argument("--model", help="override the model, e.g. sonnet, opus, haiku")
    sc2.add_argument("--provider", choices=["claude", "codex"], help="screen with Claude or a ChatGPT subscription")
    sc2.set_defaults(func=cmd_screen)

    v = sub.add_parser("validate", help="check config search_state keys/values against hiring.cafe's schema")
    v.set_defaults(func=cmd_validate)

    for name, fn, help_ in (("list", cmd_list, "print jobs from the db"), ("excel", cmd_excel, "export jobs from the db to .xlsx")):
        c = sub.add_parser(name, help=help_)
        c.add_argument("--since", type=float, metavar="HOURS", help="first seen within the last N hours")
        c.add_argument("--date", metavar="YYYY-MM-DD", help="first seen on this local date")
        if name == "excel":
            c.add_argument("--out", type=Path)
        else:
            c.add_argument("--source", choices=["hiringcafe", "simplify", "startupjobs", "applyguy"], help="only this source")
        c.set_defaults(func=fn)

    s = sub.add_parser("serve", help="local web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(func=cmd_serve)

    pr = sub.add_parser("profile", help="applicant profile used by the apply engine")
    pr.add_argument("action", choices=["init", "status"])
    pr.add_argument("--force", action="store_true", help="overwrite an existing profile.json with the template")
    pr.set_defaults(func=cmd_profile)

    ap = sub.add_parser("apply", help="queue and run applications with Claude or Codex")
    ap.add_argument("action", choices=["queue", "list", "worker"])
    ap.add_argument("job_id", nargs="?", help="job id prefix (for queue)")
    ap.add_argument("--once", action="store_true", help="worker: process one application then exit")
    ap.add_argument("--engine", choices=APPLICATION_ENGINES, help="queue provider (existing applications retain theirs)")
    ap.add_argument("--db", type=Path, help="alternate database, e.g. for isolated tests")
    ap.set_defaults(func=cmd_apply)

    sc = sub.add_parser("schedule", help="foreground loop that runs `run` every --interval seconds")
    sc.add_argument("--interval", type=int, default=3600)
    sc.add_argument("--wait", action="store_true", help="wait one interval before the first run")
    sc.set_defaults(func=cmd_schedule)
    return p


def main(argv: list[str] | None = None) -> None:
    # Windows consoles default to a legacy code page; job titles contain non-ASCII.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else list(argv)
    if not (SUBCOMMANDS & set(argv)) and not ({"-h", "--help"} & set(argv)):
        # bare `python -m jobFilter [--config X] [URL]` == run
        i = argv.index("--config") + 2 if "--config" in argv else 0
        argv = argv[:i] + ["run"] + argv[i:]
    args = parser.parse_args(argv)
    sys.exit(args.func(args))
