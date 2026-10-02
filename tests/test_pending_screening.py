import json
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from jobFilter import screen, server
from jobFilter.models import Job
from jobFilter.store import Store


class PendingScreeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'jobs.db')
        self.addCleanup(self.store.close)
        job = Job.from_hit({'objectID': 'test'})
        job.title, job.company = 'Engineer', 'Example'
        self.store.upsert_many([job], 'test')
        (self.root / 'search.json').write_text('{}')

    def test_pending_survives_rescan_and_restart_without_queuing(self):
        self.store.save_job_review('test', {'pending': True, 'conclusion': 'suitable', 'custom_tags': ['referral']})
        self.store.save_screening('test', {'status': 'ok', 'statement': 'no_sponsorship'})
        self.store.save_job_review('test', {'tags': {'new_grad': 'yes'}})
        self.store.upsert_many([Job.from_hit({'objectID': 'test'})], 'rescan')
        reopened = Store(self.store.path)
        try:
            review = reopened.get('test')['review']
            self.assertTrue(review['pending'])
            self.assertEqual(review['state'], 'suitable')
            self.assertEqual(reopened.list_applications(), [])
            reopened.save_job_review('test', {'pending': False})
            self.assertEqual(reopened.get('test')['review']['custom_tags'], ['referral'])
        finally:
            reopened.close()

    def test_starting_or_finishing_clears_pending_only(self):
        for action in ('queue_application', 'mark_submitted', 'mark_unavailable'):
            with self.subTest(action=action):
                self.store.conn.execute('DELETE FROM applications')
                self.store.conn.commit()
                self.store.save_job_review('test', {'pending': True, 'custom_tags': ['keep']})
                getattr(self.store, action)('test')
                self.assertFalse(self.store.get('test')['review']['pending'])
                self.assertEqual(self.store.get('test')['review']['custom_tags'], ['keep'])
                with self.assertRaises(ValueError):
                    self.store.save_job_review('test', {'pending': True})
        for value in ('true', 1, None, []):
            with self.assertRaises(ValueError):
                self.store.save_job_review('test', {'pending': value})

    def start_server(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(self.store, self.root / 'search.json'))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        def call(path, body=None):
            req = urllib.request.Request(f'http://127.0.0.1:{httpd.server_port}' + path,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)
        return call

    def test_api_pending_includes_old_jobs_and_rescreen_waits_for_new_result(self):
        call = self.start_server()
        self.store.conn.execute("UPDATE jobs SET first_seen='2020-01-01T00:00:00+00:00'")
        self.store.conn.commit()
        call('/api/jobs/review', {'job_id': 'test', 'pending': True})
        self.assertEqual(call('/api/jobs?since=24'), [])
        self.assertEqual([r['id'] for r in call('/api/jobs?pending=1')], ['test'])
        self.store.save_screening('test', {'status': 'ok', 'summary': 'old result'})
        release = threading.Event()
        self.addCleanup(release.set)
        def fake(local, row, cfg):
            if not release.wait(5):
                raise RuntimeError('test timed out')
            local.save_screening(row['id'], {'status': 'ok', 'summary': 'new result'})
        with patch.object(screen, 'screen_job', side_effect=fake) as run:
            try:
                self.assertTrue(call('/api/screen', {'job_id': 'test'})['started'])
                running = call('/api/job?id=test')
                self.assertTrue(running['screening_running'])
                self.assertEqual(running['screening']['summary'], 'old result')
                self.assertTrue(call('/api/jobs')[0]['screening_running'])
                self.assertEqual(call('/api/screen', {'job_id': 'test'}), {'started': False, 'running': True})
            finally:
                release.set()
            for _ in range(100):
                done = call('/api/job?id=test')
                if not done['screening_running']:
                    break
                time.sleep(.01)
            self.assertFalse(done['screening_running'])
            self.assertEqual(done['screening']['summary'], 'new result')
            self.assertTrue(done['review']['pending'])
            self.assertEqual(run.call_count, 1)
        with patch.object(screen, 'screen_job', side_effect=screen.CodexUnavailable('usage limit')):
            call('/api/screen', {'job_id': 'test'})
            for _ in range(100):
                done = call('/api/job?id=test')
                if not done['screening_running']:
                    break
                time.sleep(.01)
            self.assertFalse(done['screening_running'])
            self.assertEqual(done['screening']['status'], 'failed')
            self.assertIn('usage limit', done['screening']['summary'])
