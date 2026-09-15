"""Minimal local web UI over the SQLite store. Stdlib only.

    python -m jobFilter serve            # http://127.0.0.1:8765

Endpoints: GET /            -> static/index.html
           GET /api/dates   -> [{date, count}]
           GET /api/jobs?since=24  (hours)  or  ?date=YYYY-MM-DD  or nothing (all)
           GET /api/job?id=<id>
           GET /api/runs
"""
from __future__ import annotations

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jobFilter.store import Store

STATIC_DIR = Path(__file__).parent / "static"


def make_handler(store: Store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter default log
            if "/api/" in (args[0] if args else ""):
                return
            super().log_message(fmt, *args)

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200) -> None:
            self._send(status, json.dumps(obj).encode(), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            url = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            if url.path in ("/", "/index.html"):
                self._send(200, (STATIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/dates":
                self._json(store.dates())
            elif url.path == "/api/jobs":
                since = q.get("since")
                rows = store.query(since_hours=float(since) if since else None, date=q.get("date"))
                self._json(rows)
            elif url.path == "/api/job":
                row = store.get(q.get("id", ""))
                self._json(row or {"error": "not found"}, 200 if row else 404)
            elif url.path == "/api/runs":
                self._json(store.runs())
            else:
                self._json({"error": "not found"}, 404)

    return Handler


def serve(db_path: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    store = Store(db_path)
    httpd = ThreadingHTTPServer((host, port), make_handler(store))
    print(f"jobFilter UI: http://{host}:{port}  (db: {db_path}, {store.count()} jobs)  Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        store.close()
