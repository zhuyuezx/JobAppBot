"""Local web UI + JSON API over the SQLite store. Stdlib only.

    python -m jobFilter serve            # http://127.0.0.1:8765

Jobs:          GET /api/dates, /api/jobs?since=24|date=YYYY-MM-DD, /api/job?id=, /api/runs
Screening:     POST /api/screen {job_id}  (background; /api/jobs rows carry `screening`)
Applications:  GET /api/engine, /api/applications?status=, /api/application?id=, /api/log?id=&lines=
               POST /api/applications/queue {job_id}, /status {job_id,status,note}, /retry {job_id}
               POST /api/questions/answer {id, answer, save}
Profile:       GET/POST /api/profile, GET/POST /api/settings {model,max_turns}, GET /api/answers, POST /api/answers {question,answer}, POST /api/answers/delete {id}
Files:         GET /api/file?path=   (png/txt under data/apply only)
"""
from __future__ import annotations

import json
import mimetypes
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from jobFilter import apply_engine, profile as prof
from jobFilter.store import APP_STATUSES, Store

STATIC_DIR = Path(__file__).parent / "static"


def make_handler(store: Store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if "/api/" in (args[0] if args else ""):
                return
            super().log_message(fmt, *args)

        # ----- helpers ------------------------------------------------
        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: Any, status: int = 200) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _body(self) -> dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw or b"{}")
            except ValueError:
                return {}

        # ----- GET ----------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802
            url = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            p = url.path
            try:
                if p in ("/", "/index.html"):
                    self._send(200, (STATIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
                elif p == "/api/dates":
                    self._json(store.dates())
                elif p == "/api/jobs":
                    since = q.get("since")
                    rows = store.query(since_hours=float(since) if since else None, date=q.get("date"))
                    apps = {a["job_id"]: a["status"] for a in store.list_applications()}
                    screens = store.screening_map([r["id"] for r in rows])
                    for r in rows:
                        r["app_status"] = apps.get(r["id"])
                        r["screening"] = screens.get(r["id"])
                    self._json(rows)
                elif p == "/api/job":
                    row = store.get(q.get("id", ""))
                    self._json(row or {"error": "not found"}, 200 if row else 404)
                elif p == "/api/runs":
                    self._json(store.runs())
                elif p == "/api/engine":
                    st = apply_engine.engine_status()
                    st["counts"] = store.application_counts()
                    st["open_questions"] = len(store.questions(status="open"))
                    self._json(st)
                elif p == "/api/applications":
                    apps = store.list_applications(status=q.get("status"))
                    open_q = {}
                    for oq in store.questions(status="open"):
                        open_q[oq["job_id"]] = open_q.get(oq["job_id"], 0) + 1
                    for a in apps:
                        a["open_questions"] = open_q.get(a["job_id"], 0)
                    self._json(apps)
                elif p == "/api/application":
                    a = store.get_application(q.get("id", ""))
                    if not a:
                        return self._json({"error": "not found"}, 404)
                    a["questions"] = store.questions(job_id=a["job_id"])
                    a["log"] = _tail(a.get("log_path"), 60)
                    self._json(a)
                elif p == "/api/log":
                    a = store.get_application(q.get("id", ""))
                    self._json({"lines": _tail(a.get("log_path") if a else None, int(q.get("lines", 200)))})
                elif p == "/api/profile":
                    self._json({"profile": prof.load_profile(), "status": prof.status()})
                elif p == "/api/settings":
                    self._json(apply_engine.effective_settings())
                elif p == "/api/answers":
                    self._json(prof.load_answers())
                elif p == "/api/file":
                    path = Path(q.get("path", "")).resolve()
                    if not str(path).startswith(str(apply_engine.APPLY_DIR.resolve())) or not path.exists():
                        return self._json({"error": "not found"}, 404)
                    ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
                    self._send(200, path.read_bytes(), ctype)
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:  # keep the server up; report the error
                self._json({"error": str(e)}, 500)

        # ----- POST ---------------------------------------------------
        def do_POST(self) -> None:  # noqa: N802
            p = urllib.parse.urlparse(self.path).path
            b = self._body()
            try:
                if p == "/api/screen":
                    import threading
                    from jobFilter import screen
                    row = store.get(b.get("job_id", ""))
                    if not row:
                        return self._json({"error": "unknown job"}, 404)
                    cfg = json.loads((Path(__file__).resolve().parent.parent / "setup" / "search.json").read_text())
                    scfg = screen.screening_config(cfg)
                    def _go(r=row):
                        try:
                            screen.screen_job(Store(store.path), r, scfg)
                        except Exception as e:
                            Store(store.path).save_screening(r["id"], {"status": "failed", "summary": str(e)[:300], "model": scfg["model"]})
                    threading.Thread(target=_go, daemon=True).start()
                    self._json({"started": True})
                elif p == "/api/applications/queue":
                    if not store.get(b.get("job_id", "")):
                        return self._json({"error": "unknown job"}, 404)
                    apply_engine.start_worker(store.path)
                    self._json(store.queue_application(b["job_id"], b.get("engine", "claude-chrome")))
                elif p == "/api/applications/status":
                    if b.get("status") not in APP_STATUSES:
                        return self._json({"error": f"status must be one of {APP_STATUSES}"}, 400)
                    fields = {"status": b["status"]}
                    if "note" in b:
                        fields["note"] = b["note"]
                    store.update_application(b["job_id"], **fields)
                    self._json(store.get_application(b["job_id"]))
                elif p == "/api/applications/retry":
                    apply_engine.start_worker(store.path)
                    store.update_application(b["job_id"], status="queued")
                    self._json(store.get_application(b["job_id"]))
                elif p == "/api/questions/answer":
                    qrow = store.answer_question(int(b["id"]), b.get("answer", ""))
                    if not qrow:
                        return self._json({"error": "unknown question"}, 404)
                    if b.get("save", True) and b.get("answer"):
                        prof.add_answer(qrow["question"], b["answer"])
                    requeued = apply_engine.requeue_if_answered(store, qrow["job_id"])
                    if requeued:
                        apply_engine.start_worker(store.path)
                    self._json({"question": qrow, "requeued": requeued})
                elif p == "/api/profile":
                    if not isinstance(b.get("profile"), dict):
                        return self._json({"error": "profile must be an object"}, 400)
                    prof.save_profile(b["profile"])
                    self._json({"ok": True, "status": prof.status()})
                elif p == "/api/settings":
                    self._json(prof.save_settings(b))
                elif p == "/api/answers":
                    if not b.get("question"):
                        return self._json({"error": "question required"}, 400)
                    self._json(prof.add_answer(b["question"], b.get("answer", "")))
                elif p == "/api/answers/delete":
                    self._json({"deleted": prof.delete_answer(int(b["id"]))})
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:
                self._json({"error": str(e)}, 500)

    return Handler


def _tail(path: str | None, n: int) -> list[str]:
    if not path or not Path(path).exists():
        return []
    lines = Path(path).read_text(errors="replace").splitlines()
    return lines[-n:]


def serve(db_path: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    store = Store(db_path)
    if store.application_counts().get("queued"):
        apply_engine.start_worker(store.path)
    httpd = ThreadingHTTPServer((host, port), make_handler(store))
    print(f"jobFilter UI: http://{host}:{port}  (db: {db_path}, {store.count()} jobs)  Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        store.close()
