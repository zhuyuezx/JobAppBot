"""Interval loop: run a scan every N seconds in the foreground. Works on any OS.

    python -m jobFilter schedule --interval 3600

For a background service that survives logouts/reboots use `bin/jobfilter`
(macOS launchd, Linux systemd) or Task Scheduler on Windows; see README.
Each tick calls `run --new-only`, which dedups against data/jobs.db and
regenerates today's data/excel/YYYY-MM-DD.xlsx.
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def run_once(config: Path) -> int:
    cmd = [sys.executable, "-m", "jobFilter", "--config", str(config), "run", "--new-only"]
    return subprocess.call(cmd, cwd=Path(__file__).resolve().parent.parent)


def run_forever(interval: int = 3600, config: Path | None = None, run_immediately: bool = True) -> None:
    config = config or Path(__file__).resolve().parent.parent / "config" / "search.json"
    if not run_immediately:
        time.sleep(interval)
    while True:
        started = time.monotonic()
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] tick", file=sys.stderr, flush=True)
        try:
            code = run_once(config)
            if code:
                print(f"run exited with {code}", file=sys.stderr, flush=True)
        except Exception as e:  # keep the loop alive
            print(f"run crashed: {e}", file=sys.stderr, flush=True)
        sleep_for = max(0.0, interval - (time.monotonic() - started))
        try:
            time.sleep(sleep_for)
        except KeyboardInterrupt:
            print("stopped", file=sys.stderr)
            return
