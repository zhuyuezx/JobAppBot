"""Subscription-backed structured Codex runs, with optional local browser tools."""
from __future__ import annotations

import copy
import glob
import json
import os
import shutil
import subprocess
import tempfile
import queue
import threading
import time
from pathlib import Path

from jsonschema import validate
from jobFilter.bridge import validate_endpoint
from jobFilter.providers import validate_thinking_level


class CodexUnavailable(RuntimeError):
    """Authentication or quota prevents screening; leave jobs available to retry."""


class CodexCancelled(RuntimeError):
    pass


def _browser_process(cmd, prompt, env, timeout, log, cancelled):
    """Stream tool events while retaining timeout and cancellation control."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env)
    lines, messages = [], queue.Queue()
    def read():
        for line in proc.stdout:
            messages.put(line)
        messages.put(None)
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        proc.stdin.write(prompt); proc.stdin.close()
        deadline = time.monotonic() + timeout
        while True:
            if cancelled and cancelled():
                raise CodexCancelled("Application cancelled")
            if time.monotonic() > deadline:
                raise TimeoutError("Codex application timed out")
            try:
                line = messages.get(timeout=0.3)
            except queue.Empty:
                continue
            if line is None:
                break
            lines.append(line)
            try:
                event = json.loads(line)
                item = event.get("item") or {}
                if event.get("type") == "item.completed":
                    # Tool arguments/results may include form data; log only progress, not credentials.
                    log(f"[{item.get('type', 'event')}] {item.get('tool') or item.get('name') or 'completed'}")
                elif event.get("type") in ("turn.started", "turn.completed", "turn.failed"):
                    log(event["type"])
            except ValueError:
                pass
        proc.wait(timeout=max(1, deadline - time.monotonic()))
        return subprocess.CompletedProcess(cmd, proc.returncode, "".join(lines), "")
    finally:
        if proc.poll() is None:
            proc.kill(); proc.wait()
        reader.join(timeout=2)
        proc.stdout.close()


def find_codex():
    override = os.environ.get("JOBFILTER_CODEX")
    if override:
        return override if os.access(override, os.X_OK) else None
    candidates = [shutil.which("codex"),
                  "/Applications/ChatGPT.app/Contents/Resources/codex",
                  "/Applications/Codex.app/Contents/Resources/codex"]
    candidates += sorted(glob.glob(str(Path.home() / ".vscode/extensions/openai.chatgpt-*/bin/*/codex")), reverse=True)
    return next((p for p in candidates if p and os.access(p, os.X_OK)), None)


def strict_schema(schema):
    result = copy.deepcopy(schema)
    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(result)
    return result


def run_codex(prompt, schema, model="", timeout=300, *, reasoning_effort="", browser_url=None, log=lambda _: None, cancelled=None):
    validate_thinking_level(reasoning_effort)
    binary = find_codex()
    if not binary:
        raise CodexUnavailable("Codex not found; install it or set JOBFILTER_CODEX, then run codex login.")
    with tempfile.TemporaryDirectory(prefix="jobfilter-codex-") as tmp:
        schema_path, output = Path(tmp) / "schema.json", Path(tmp) / "result.json"
        schema_path.write_text(json.dumps(strict_schema(schema)))
        cmd = [binary, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
               "--sandbox", "read-only", "--json", "--color", "never", "-C", str(Path(__file__).resolve().parent.parent) if browser_url else tmp,
               "--output-schema", str(schema_path), "--output-last-message", str(output),
               "-c", 'forced_login_method="chatgpt"', "-c", 'web_search="disabled"' if browser_url else 'web_search="live"',
               "-c", "features.shell_tool=false", "-c", "features.apply_patch_freeform=false"]
        if browser_url:
            validate_endpoint(browser_url)
            for setting in [f"url={json.dumps(browser_url)}", "required=true", "tool_timeout_sec=90",
                            'default_tools_approval_mode="approve"',
                            'disabled_tools=["browser_close","browser_install"]']:
                cmd += ["-c", "mcp_servers.jobfilter_browser." + setting]
        if reasoning_effort:
            cmd += ["-c", "model_reasoning_effort=" + json.dumps(reasoning_effort)]
        if model:
            cmd += ["--model", model]
        cmd.append("-")
        env = {k: v for k, v in os.environ.items()
               if k not in {"OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_THREAD_ID"}}
        proc = (_browser_process(cmd, prompt, env, timeout, log, cancelled) if browser_url else
                subprocess.run(cmd, input=prompt, capture_output=True, text=True, env=env, timeout=timeout))
        events = []
        for line in proc.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        errors = [e.get("message") or (e.get("error") or {}).get("message", "")
                  for e in events if e.get("type") in {"error", "turn.failed"}]
        if proc.returncode or errors or not output.exists():
            message = ("; ".join(errors) or proc.stderr or "Codex returned no result")[-2000:]
            if any(s in message.lower() for s in ("usage limit", "rate limit", "quota", "not logged in", "please log in", "refresh token")):
                raise CodexUnavailable(message)
            raise RuntimeError(message)
        result = json.loads(output.read_text())
        validate(result, schema)
        usage = next((e.get("usage") for e in reversed(events) if e.get("type") == "turn.completed"), None)
        return result, {"model": "codex/" + (model or "default"), "usage": usage,
                        "total_cost_usd": None, "reasoning_effort": reasoning_effort or "default",
                        "tool_types": sorted({e.get("item", {}).get("type", "") for e in events
                                              if e.get("type") == "item.completed"})}
