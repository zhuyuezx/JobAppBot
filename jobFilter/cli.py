"""Commands:

    python -m jobFilter run [URL] [--no-store] [--xlsx PATH]   fetch -> rules -> store -> print list -> daily excel
    python -m jobFilter validate                               check config/search.json against hiring.cafe's schema
    python -m jobFilter list [--since 24 | --date YYYY-MM-DD]  read back from the local db
    python -m jobFilter excel [--date YYYY-MM-DD | --since 24] [--out PATH]
    python -m jobFilter serve [--port 8765]                    local web UI
    python -m jobFilter schedule [--interval 3600]             foreground loop: run every N seconds
"""
from __future__ import annotations

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
from jobFilter.hiringcafe import HiringCafeClient, HiringCafeError
from jobFilter.models import Job

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "search.json"
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
    return f"{posted}  {j.title} | {j.company} | {j.location or '?'} | {visa}\n    {j.apply_url or j.hc_url}"


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

    client = HiringCafeClient()
    log(f"[{run_id}] fetching {client.search_url(search_state)}")
    try:
        jobs = [Job.from_hit(h) for h in client.search(search_state, max_pages=args.max_pages)]
    except HiringCafeError as e:
        log(f"fetch failed: {e}")
        return 2

    kept, rejected = apply_rules(jobs, rules)
    log(f"{len(jobs)} fetched, {len(kept)} kept, {len(rejected)} rejected")
    for reason, n in Counter(r for _, r in rejected).most_common():
        log(f"  - {reason}: {n}")
    kept.sort(key=lambda j: j.published_millis or 0, reverse=True)

    to_print = kept
    if not args.no_store:
        from jobFilter.store import Store
        store = Store(DB_PATH)
        new = store.upsert_many(kept, run_id)
        store.record_run(run_id, search_state, len(jobs), len(kept), len(new))
        log(f"{len(new)} new (db now {store.count()} jobs)")
        if args.new_only:
            to_print = new
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
    for r in rows:
        j = r["job"]
        print(f"{r['first_seen'][:16].replace('T',' ')}  {j['title']} | {j['company']} | {j.get('location') or '?'}\n    {j.get('apply_url') or r['hc_url']}")
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


# ----- parser -------------------------------------------------------------
SUBCOMMANDS = {"run", "validate", "list", "excel", "serve", "schedule"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobFilter", description="hiring.cafe fetch -> filter -> collect")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="fetch, filter, store, print list, write daily excel")
    r.add_argument("url", nargs="?", help="hiring.cafe search URL (default: config search_state)")
    r.add_argument("--no-rules", action="store_true", help="skip the rules block")
    r.add_argument("--no-store", action="store_true", help="print only, don't touch the db or daily excel")
    r.add_argument("--new-only", action="store_true", help="print only jobs not seen in earlier runs")
    r.add_argument("--xlsx", type=Path, help="also write this run's list to an .xlsx file")
    r.add_argument("--max-pages", type=int, default=25)
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("validate", help="check config search_state keys/values against hiring.cafe's schema")
    v.set_defaults(func=cmd_validate)

    for name, fn, help_ in (("list", cmd_list, "print jobs from the db"), ("excel", cmd_excel, "export jobs from the db to .xlsx")):
        c = sub.add_parser(name, help=help_)
        c.add_argument("--since", type=float, metavar="HOURS", help="first seen within the last N hours")
        c.add_argument("--date", metavar="YYYY-MM-DD", help="first seen on this local date")
        if name == "excel":
            c.add_argument("--out", type=Path)
        c.set_defaults(func=fn)

    s = sub.add_parser("serve", help="local web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(func=cmd_serve)

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
