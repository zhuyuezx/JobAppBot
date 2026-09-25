import io
import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from jobFilter import apply_engine, application_state, duplicates, profile, server
from jobFilter.models import Job
from jobFilter.store import Store


def add_job(store, job_id, title="Software Engineer - New Grad", company="SeatGeek", location="New York City, NY", via="applyguy"):
    job = Job.from_hit({"objectID": job_id})
    job.title, job.company, job.location, job.via, job.apply_url = title, company, location, via, f"https://example.com/{job_id}"
    store.upsert_many([job], "test")


def review_ready():
    return {"status": "review_ready", "summary": "Filled; not submitted.", "page_url": "https://example.com/job",
            "filled_fields": ["Name"], "unanswered_questions": []}


class DuplicateTests(unittest.TestCase):
    def test_same_posting_from_other_sources_is_tagged(self):
        rows = [
            {"id": "ag", "via": "applyguy", "company": "SeatGeek", "title": "Software Engineer - New Grad", "location": "New York City, NY", "first_seen": "2026-09-23"},
            {"id": "sj", "via": "startupjobs", "company": "SeatGeek", "title": "Software Engineer - New Grad", "location": "New York, U.S.", "first_seen": "2026-09-24"},
            {"id": "intern", "via": "startupjobs", "company": "SeatGeek", "title": "Software Engineer - Internship", "location": "New York, U.S.", "first_seen": "2026-09-24"},
            {"id": "visa-austin", "via": "hiringcafe", "company": "Visa Inc.", "title": "Software Engineer, New Graduate", "location": "Austin, TX", "first_seen": "2026-09-20"},
            {"id": "visa-sf", "via": "simplify", "company": "Visa", "title": "Software Engineer New Grad", "location": "San Francisco, CA", "first_seen": "2026-09-21"},
            {"id": "visa-austin-2", "via": "simplify", "company": "Visa", "title": "Software Engineer (New Grad)", "location": "Austin, Texas, United States", "first_seen": "2026-09-21"},
            {"id": "visa-austin-3", "via": "simplify", "company": "Visa", "title": "Software Engineer (New Grad)", "location": "Austin, Texas", "first_seen": "2026-09-22"},
            {"id": "fn-us", "via": "startupjobs", "company": "FiscalNote", "title": "Associate Software Engineer", "location": "U.S.", "first_seen": "2026-09-22"},
            {"id": "fn-remote", "via": "simplify", "company": "FiscalNote", "title": "Associate Software Engineer", "location": "Remote in USA", "first_seen": "2026-09-21"},
        ]
        found = {k: [c["id"] for c in v] for k, v in duplicates.find(rows, rows).items()}
        self.assertEqual(found["sj"], ["ag"])
        self.assertEqual(found["ag"], ["sj"])
        self.assertNotIn("intern", found)
        self.assertNotIn("visa-sf", found, "the same title in another city is another position")
        self.assertEqual(found["visa-austin"], ["visa-austin-2", "visa-austin-3"])
        self.assertEqual(found["visa-austin-2"], ["visa-austin"], "one source's own listings are never tagged")
        self.assertEqual(found["fn-us"], ["fn-remote"])
        self.assertEqual(duplicates.cities("Seattle, WA; SF; NYC"), {"seattle", "san francisco", "new york"})
        self.assertEqual(duplicates.cities("San Francisco Bay Area"), {"san francisco"})
        self.assertEqual(duplicates.title_key("Software Engineer II (New Graduate)"), duplicates.title_key("software engineer 2 - new grad"))


class ApplicationControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "jobs.db")
        self.patches = [patch.object(profile, "SETTINGS_PATH", self.root / "settings.json"),
                        patch.object(profile, "load_profile", return_value={"name": "Alex Example"}),
                        patch.object(profile, "load_answers", return_value=[]),
                        patch.object(profile, "resume_path", return_value=None),
                        patch.object(profile, "resume_text", return_value=""),
                        patch.object(profile, "COVER_LETTER_DIR", self.root / "cover_letter"),
                        patch.object(application_state, "APPLY_DIR", self.root / "apply"),
                        patch.object(apply_engine, "SKILL_PATH", self.root / "skill.md"),
                        patch.object(apply_engine, "start_worker")]
        for p in self.patches:
            p.start()
        add_job(self.store, "test")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.store.close()
        self.tmp.cleanup()

    def test_stop_ends_queued_and_orphaned_runs(self):
        self.store.queue_application("test")
        self.assertEqual(apply_engine.stop_application(self.store, "test")["summary"], apply_engine.STOP_SUMMARY)
        self.assertEqual(self.store.get_application("test")["status"], "failed")
        with self.assertRaisesRegex(ValueError, "Only a queued or running"):
            apply_engine.stop_application(self.store, "test")
        with self.assertRaisesRegex(ValueError, "Unknown application"):
            apply_engine.stop_application(self.store, "missing")
        self.store.update_application("test", status="running")   # left behind by a restart: no process
        self.assertEqual(apply_engine.stop_application(self.store, "test")["status"], "failed")

    def test_stopping_a_claude_run_terminates_it_and_keeps_the_stop(self):
        app = self.store.queue_application("test")
        proc = Mock(returncode=-15)
        proc.poll.return_value = None
        def events():
            yield json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}) + "\n"
            apply_engine.stop_application(self.store, "test")          # the user clicks Stop mid-run
            yield json.dumps({"type": "result", "structured_output": review_ready(), "session_id": "s1"}) + "\n"
        proc.stdout = events()
        with patch.object(apply_engine, "find_claude", return_value="claude"), \
                patch.object(apply_engine.subprocess, "Popen", return_value=proc):
            self.assertEqual(apply_engine.run_application(self.store, app)["status"], "stopped")
        proc.terminate.assert_called_once()
        current = self.store.get_application("test")
        self.assertEqual((current["status"], current["summary"]), ("failed", apply_engine.STOP_SUMMARY))
        self.assertEqual(apply_engine._processes, {})
        self.assertIn("[stopped]", (self.root / "apply" / "test" / "log.txt").read_text())

    def test_unavailable_mark_stops_and_clears(self):
        with self.assertRaisesRegex(ValueError, "Unknown job"):
            self.store.mark_unavailable("missing")
        marked = self.store.mark_unavailable("test", "posting closed")
        self.assertEqual((marked["status"], marked["engine"], marked["note"]), ("unavailable", "manual", "posting closed"))
        self.assertIsNone(self.store.clear_unavailable("test"), "a record that only held the mark goes away")
        self.assertIsNone(self.store.get_application("test"))

        self.store.queue_application("test")
        self.store.update_application("test", status="needs_answer")
        self.store.add_question("test", "Start date?")
        self.assertEqual(self.store.mark_unavailable("test")["status"], "unavailable")
        self.assertEqual(self.store.questions(job_id="test", status="open"), [])
        self.assertEqual(self.store.clear_unavailable("test")["status"], "skipped")
        with self.assertRaisesRegex(ValueError, "not marked unavailable"):
            self.store.clear_unavailable("test")
        self.store.update_application("test", status="running")
        with self.assertRaisesRegex(ValueError, "Stop the active"):
            self.store.mark_unavailable("test")

    def test_agent_can_report_unavailable(self):
        app = self.store.queue_application("test")
        out = {**review_ready(), "status": "unavailable", "summary": "Page says 'This vacancy has now expired.'"}
        proc = Mock(stdout=io.StringIO(json.dumps({"type": "result", "structured_output": out}) + "\n"), returncode=0)
        with patch.object(apply_engine, "find_claude", return_value="claude"), patch.object(apply_engine.subprocess, "Popen", return_value=proc):
            self.assertEqual(apply_engine.run_application(self.store, app)["status"], "unavailable")
        self.assertIn("unavailable", application_state.RESULT_SCHEMA["properties"]["status"]["enum"])

    def test_routes_stop_delete_mark_unavailable_and_tag_duplicates(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.store, self.root / "search.json"))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_port}"
        def call(path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                return json.loads(e.read())

        add_job(self.store, "later", location="New York, U.S.", via="startupjobs")
        self.store.mark_submitted("test")
        jobs = {r["id"]: r for r in call("/api/jobs")}
        self.assertEqual([(d["id"], d["app_status"]) for d in jobs["later"]["duplicates"]], [("test", "submitted")])

        self.store.queue_application("later")
        self.store.update_application("later", status="running")
        self.assertEqual(call("/api/applications/unavailable", {"job_id": "later"})["status"], "unavailable")
        self.assertEqual(call("/api/applications/unavailable", {"job_id": "later", "unavailable": False})["status"], "skipped")
        self.store.update_application("later", status="queued")
        self.assertEqual(call("/api/applications/status", {"job_id": "later", "status": "skipped"})["status"], "skipped")
        self.store.update_application("later", status="running")
        self.assertEqual(call("/api/applications/stop", {"job_id": "later"})["status"], "failed")
        self.assertIn("error", call("/api/applications/stop", {"job_id": "later"}))

        work = application_state.work_directory("later")
        (work / "log.txt").write_text("old run")
        self.store.add_question("later", "Start date?")
        self.store.update_application("later", status="running")
        self.assertEqual(call("/api/applications/delete", {"job_id": "later"}), {"deleted": True})
        self.assertIsNone(self.store.get_application("later"))
        self.assertEqual(self.store.questions(job_id="later"), [])
        self.assertFalse(work.exists())
        self.assertIsNotNone(self.store.get("later"), "the job itself stays")
        self.assertIn("error", call("/api/applications/delete", {"job_id": "later"}))


if __name__ == "__main__":
    unittest.main()
