"""Managed, loopback-only Playwright MCP browser. Keeps review tabs alive across Codex runs."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_VERSION = "0.0.81"
_start_lock = threading.Lock()


def directory():
    return Path(os.environ.get("JOBFILTER_BRIDGE_DIR", ROOT / "data" / "codex-browser"))


def port():
    value = int(os.environ.get("JOBFILTER_BRIDGE_PORT", "8931"))
    if not 1024 <= value <= 65535:
        raise ValueError("JOBFILTER_BRIDGE_PORT must be between 1024 and 65535")
    return value


def endpoint():
    return f"http://127.0.0.1:{port()}/mcp"


def executable():
    node = shutil.which("node")
    if not node:
        candidates = sorted((Path.home() / ".nvm/versions/node").glob("*/bin/node"), key=lambda p: p.stat().st_mtime, reverse=True)
        node = str(candidates[0]) if candidates else None
    cli = ROOT / "data/browser-tools/node_modules/@playwright/mcp/cli.js"
    return node, cli


def status():
    node, cli = executable()
    running = False
    try:
        state = json.loads((directory() / "state.json").read_text())
        if state["port"] == port():
            os.kill(state["pid"], 0)
            with socket.create_connection(("127.0.0.1", port()), timeout=0.2):
                running = True
    except (OSError, ValueError, KeyError):
        pass
    return {"installed": bool(node and cli.is_file()), "running": running,
            "profile": str(directory() / "profile"), "url": endpoint()}


class Session:
    """Minimal Streamable HTTP MCP client; the daemon retains an owner session."""
    def __init__(self, url):
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path != "/mcp" or parsed.username or parsed.query or parsed.fragment:
            raise ValueError("Browser bridge must use http://127.0.0.1:PORT/mcp")
        self.url, self.session_id, self.sequence = url, None, 0
        self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "jobfilter-browser-owner", "version": "1"}})
        self.request("notifications/initialized", {}, notify=True)

    def request(self, method, params, notify=False):
        self.sequence += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            body["id"] = self.sequence
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=90) as response:
            self.session_id = response.headers.get("Mcp-Session-Id", self.session_id)
            raw = response.read().decode()
        if not raw or notify:
            return {}
        events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")] if raw.startswith("event:") or raw.startswith("data:") else [json.loads(raw)]
        message = next((e for e in events if e.get("id") == self.sequence), {})
        if "error" in message:
            raise RuntimeError(str(message["error"]))
        return message.get("result", {})

    def call(self, name, arguments):
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(str(result.get("content")))
        return result

    def close(self):
        if self.session_id:
            request = urllib.request.Request(self.url, method="DELETE", headers={"Mcp-Session-Id": self.session_id})
            with urllib.request.urlopen(request, timeout=5):
                pass


def ensure_running():
    with _start_lock:
        if status()["running"]:
            return endpoint()
        if not status()["installed"]:
            raise RuntimeError(f"Browser bridge is not installed. Run: npm install --prefix data/browser-tools --save-exact @playwright/mcp@{PACKAGE_VERSION}")
        directory().mkdir(parents=True, exist_ok=True)
        with (directory() / "bridge.log").open("a") as log:
            child = subprocess.Popen([sys.executable, "-m", "jobFilter.bridge"], cwd=ROOT,
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if status()["running"]:
                return endpoint()
            if child.poll() is not None:
                raise RuntimeError(f"Browser bridge could not start. See {directory() / 'bridge.log'}")
            time.sleep(0.2)
        child.terminate()
        raise RuntimeError("Browser bridge startup timed out")


def serve():
    directory().mkdir(parents=True, exist_ok=True)
    # One bridge owns a profile; a second UI/CLI launch must not replace its state.
    lock = (directory() / "owner.lock").open("w")
    if os.name != "nt":
        import fcntl
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
    node, cli = executable()
    if not node or not cli.is_file():
        raise RuntimeError("Playwright MCP is not installed")
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", port()))
    finally:
        probe.close()
    cmd = [node, str(cli), "--browser", "chrome", "--host", "127.0.0.1", "--port", str(port()),
           "--allowed-hosts", f"127.0.0.1:{port()},localhost:{port()}", "--shared-browser-context",
           "--user-data-dir", str(directory() / "profile"), "--output-dir", str(directory() / "output")]
    if os.environ.get("JOBFILTER_BRIDGE_HEADLESS") == "1":
        cmd += ["--headless"]
    process = subprocess.Popen(cmd, cwd=ROOT)
    stopping = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.set())
    state_path = directory() / "state.json"
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Playwright MCP exited during startup")
            try:
                owner = Session(endpoint())
                break
            except OSError:
                stopping.wait(0.2)
        else:
            raise RuntimeError("Playwright MCP did not start")
        # Keeps the browser from closing when each short-lived Codex client exits.
        owner.call("browser_tabs", {"action": "list"})
        state_path.write_text(json.dumps({"pid": os.getpid(), "port": port()}))
        print(f"Browser bridge ready: {endpoint()}", flush=True)
        while process.poll() is None and not stopping.wait(1):
            pass
    finally:
        state_path.unlink(missing_ok=True)
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
        lock.close()


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    serve()
