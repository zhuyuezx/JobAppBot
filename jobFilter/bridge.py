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
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_VERSION = "0.0.81"
_start_lock = threading.Lock()
OWNER_PROTOCOL = 1


def directory():
    return Path(os.environ.get("JOBFILTER_BRIDGE_DIR", ROOT / "data" / "codex-browser"))


def port():
    value = int(os.environ.get("JOBFILTER_BRIDGE_PORT", "8931"))
    if not 1024 <= value <= 65535:
        raise ValueError("JOBFILTER_BRIDGE_PORT must be between 1024 and 65535")
    return value


def endpoint():
    return f"http://127.0.0.1:{port()}/mcp"


def validate_endpoint(url):
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path != "/mcp" or parsed.username or parsed.query or parsed.fragment:
        raise ValueError("Browser bridge must use http://127.0.0.1:PORT/mcp")
    return url


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
        self.url, self.session_id, self.sequence = validate_endpoint(url), None, 0
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
        current = status()
        if current["running"]:
            retain_browser()
            return current["url"]
        if not current["installed"]:
            raise RuntimeError(f"Browser bridge is not installed. Run: npm install --prefix data/browser-tools --save-exact @playwright/mcp@{PACKAGE_VERSION}")
        directory().mkdir(parents=True, exist_ok=True)
        with (directory() / "bridge.log").open("a") as log:
            child = subprocess.Popen([sys.executable, "-m", "jobFilter.bridge"], cwd=ROOT,
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if status()["running"]:
                retain_browser()
                return endpoint()
            if child.poll() is not None:
                raise RuntimeError(f"Browser bridge could not start. See {directory() / 'bridge.log'}")
            time.sleep(0.2)
        child.terminate()
        raise RuntimeError("Browser bridge startup timed out")


def retain_browser():
    """Ask the daemon to own the current browser before a short-lived agent connects.

    A browser restart invalidates the old MCP owner without stopping the daemon.
    A fresh owner must acquire it before the agent runs, and outlive that agent.
    Per-request files avoid cross-process races between the UI and worker.
    """
    state = json.loads((directory() / "state.json").read_text())
    if state.get("owner_protocol") != OWNER_PROTOCOL:
        raise RuntimeError("Browser bridge needs an update. Finish any open forms, then restart the browser bridge before running GPT again.")
    requests = directory() / "owner-requests"
    requests.mkdir(exist_ok=True)
    ticket = requests / uuid.uuid4().hex
    request, reply = ticket.with_suffix(".request"), ticket.with_suffix(".reply")
    request.touch()
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if reply.exists():
                result = json.loads(reply.read_text())
                if result.get("error"):
                    raise RuntimeError("Could not retain the application browser: " + result["error"])
                return
            time.sleep(0.1)
        raise RuntimeError("Browser owner did not respond; application was not started.")
    finally:
        request.unlink(missing_ok=True)
        reply.unlink(missing_ok=True)


def renew_owner(owner):
    """Acquire first, release last: never leave the browser without an owner."""
    replacement = Session(endpoint())
    try:
        replacement.call("browser_tabs", {"action": "list"})
    except Exception:
        try:
            replacement.close()
        except Exception:
            pass
        raise
    try:
        owner.close()
    except Exception:
        pass  # The old session may already have died with a previous browser.
    return replacement


def service_owner_requests(owner):
    for request in (directory() / "owner-requests").glob("*.request"):
        result = {}
        try:
            owner = renew_owner(owner)
        except Exception as exc:
            result = {"error": str(exc)}
        if request.exists():
            reply = request.with_suffix(".reply")
            temp = request.with_suffix(".tmp")
            temp.write_text(json.dumps(result))
            temp.replace(reply)
            request.unlink(missing_ok=True)
    return owner


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
        # A just-stopped bridge can leave TCP connections in TIME_WAIT.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port()))
    finally:
        probe.close()
    # A separate profile of Google Chrome still shares its macOS app identity,
    # so OS-opened links can land in the automation instance. Use Playwright's
    # separate Chrome for Testing application, never the user's default Chrome.
    cmd = [node, str(cli), "--browser", "chromium", "--host", "127.0.0.1", "--port", str(port()),
           "--allowed-hosts", f"127.0.0.1:{port()},localhost:{port()}", "--shared-browser-context", "--idle-timeout", "0",
           "--user-data-dir", str(directory() / "profile"), "--output-dir", str(directory() / "output")]
    if os.environ.get("JOBFILTER_BRIDGE_HEADLESS") == "1":
        cmd += ["--headless"]
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / "data/browser-tools/browsers")}
    process = subprocess.Popen(cmd, cwd=ROOT, env=env)
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
        state_path.write_text(json.dumps({"pid": os.getpid(), "port": port(), "owner_protocol": OWNER_PROTOCOL}))
        print(f"Browser bridge ready: {endpoint()}", flush=True)
        while process.poll() is None and not stopping.wait(0.2):
            owner = service_owner_requests(owner)
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
