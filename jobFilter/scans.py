"""Shared scan status and cross-process exclusion for scheduled and manual runs."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

if os.name == 'nt':
    import msvcrt
else:
    import fcntl

ROOT = Path(__file__).resolve().parent.parent


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def acquire(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    lock = (directory / 'scan.lock').open('a+b')
    try:
        if os.name == 'nt':
            if lock.tell() == 0:
                lock.write(b'\0')
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, PermissionError):
        lock.close()
        return None
    return lock  # Closing releases the lock, including after process termination.


def status(directory: Path):
    lock = acquire(directory)
    running = lock is None
    try:
        try:
            state = json.loads((directory / 'scan.json').read_text())
        except (FileNotFoundError, ValueError):
            state = {'status': 'idle', 'message': 'Scan all enabled sources now.'}
        if running:
            state['status'] = 'running'
        elif state['status'] == 'running':
            state.update(status='failed', message='Scan was interrupted. You can retry.')
        return {**state, 'running': running}
    finally:
        if lock:
            lock.close()


class Run:
    """Called only while holding the scan lock."""
    def __init__(self, directory):
        self.directory = directory
        self.state = {'status': 'running', 'started_at': now(), 'message': 'Starting scan…'}
        self.update()

    def update(self, **changes):
        self.state.update(changes)
        path = self.directory / 'scan.json'
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.state))
        tmp.replace(path)

    def finish(self, code, error=None):
        failed = bool(code)
        warnings = self.state.get('source_errors', {})
        message = error or (self.state.get('message') if failed else
                           f"Scan complete · {self.state.get('new_jobs', 0)} new jobs saved")
        if not failed and warnings:
            message += ' · some sources failed'
        self.update(status='failed' if failed else 'complete', finished_at=now(), message=message)


class Controller:
    """Non-blocking UI launcher; the CLI owns the lock for the entire pipeline."""
    def __init__(self, db_path: Path, config_path: Path):
        self.db_path, self.config_path = db_path.resolve(), config_path.resolve()
        self.directory = self.db_path.parent
        self.process = None
        self.started_before = None
        self.lock = threading.Lock()

    def _status(self):
        state = status(self.directory)
        if self.process is not None:
            code = self.process.poll()
            if code is None and not state['running']:
                return {**state, 'running': True, 'status': 'running', 'message': 'Starting scan…'}
            if code and not state['running'] and state.get('started_at') == self.started_before:
                return {**state, 'status': 'failed', 'message': 'Scan failed to start. See data/scan.log and retry.'}
        return state

    def status(self):
        with self.lock:
            return self._status()

    def start(self):
        with self.lock:
            state = self._status()
            if state['running']:
                return {**state, 'started': False}
            self.started_before = state.get('started_at')
            with (self.directory / 'scan.log').open('a') as log:
                self.process = subprocess.Popen(
                    [sys.executable, '-m', 'jobFilter', '--config', str(self.config_path),
                     'run', '--new-only', '--db', str(self.db_path)],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            return {**self._status(), 'started': True}
