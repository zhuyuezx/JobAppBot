import contextlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from jobFilter import cli, scans, server, sources
from jobFilter.models import Job
from jobFilter.store import Store


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'jobs.db'
        self.config = self.root / 'search.json'
        self.config.write_text(json.dumps({'search_state': {}, 'rules': {},
            'sources': {name: False for name in ('hiringcafe', 'simplify', 'startupjobs', 'applyguy', 'speedyapply')},
            'screening': {'enabled': False}}))

    def args(self):
        return cli.build_parser().parse_args(['--config', str(self.config), 'run', '--db', str(self.db)])

    def test_scheduled_and_manual_scans_share_cross_process_lock(self):
        with scans.acquire(self.root):
            run = scans.Run(self.root)
            run.update(message='Fetching jobs…')
            controller = scans.Controller(self.db, self.config)
            with patch.object(scans.subprocess, 'Popen') as start:
                self.assertFalse(controller.start()['started'])
                start.assert_not_called()
            child = subprocess.run([sys.executable, '-c',
                'from pathlib import Path; from jobFilter import scans; import sys; '
                'assert scans.acquire(Path(sys.argv[1])) is None', str(self.root)], capture_output=True)
            self.assertEqual(child.returncode, 0, child.stderr)
            with patch.object(cli, '_run') as pipeline, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.cmd_run(self.args()), 0)
                pipeline.assert_not_called()
        self.assertEqual(scans.status(self.root)['status'], 'failed', 'An abandoned run must be retryable')
        with scans.acquire(self.root):
            retry = scans.Run(self.root)
            retry.finish(0)
        self.assertEqual(scans.status(self.root)['status'], 'complete')

    def test_double_click_only_starts_one_child_and_spawn_failure_can_retry(self):
        controller = scans.Controller(self.db, self.config)
        proc = Mock()
        proc.poll.return_value = None
        with patch.object(scans.subprocess, 'Popen', side_effect=OSError('cannot start')):
            with self.assertRaises(OSError):
                controller.start()
        with patch.object(scans.subprocess, 'Popen', return_value=proc) as start:
            self.assertTrue(controller.start()['started'])
            self.assertFalse(controller.start()['started'])
            self.assertTrue(controller.status()['running'])
            start.assert_called_once()
            command = start.call_args.args[0]
            self.assertIn(str(self.config.resolve()), command)
            self.assertIn(str(self.db.resolve()), command)

    def test_pipeline_stores_jobs_and_reports_partial_source_failure(self):
        job = Job.from_hit({'objectID': 'scan-test'})
        job.title, job.company = 'Software Engineer', 'Example'
        with patch.object(sources, 'fetch_all', return_value=([job], {'simplify': 1}, {'hiringcafe': 'temporarily unavailable'})), \
             contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_run(self.args()), 0)
        state = scans.status(self.root)
        self.assertFalse(state['running'])
        self.assertEqual(state['new_jobs'], 1)
        self.assertEqual(state['kept'], 1)
        self.assertIn('some sources failed', state['message'])
        self.assertEqual(state['source_errors']['hiringcafe'], 'temporarily unavailable')
        store = Store(self.db)
        self.addCleanup(store.close)
        self.assertEqual(store.count(), 1)
        self.assertTrue(list((self.root / 'excel').glob('*.xlsx')))

    def test_pipeline_exception_and_all_sources_failed_release_lock(self):
        with patch.object(sources, 'fetch_all', return_value=([], {}, {'hiringcafe': 'offline'})), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_run(self.args()), 2)
        self.assertEqual(scans.status(self.root)['status'], 'failed')
        self.assertIn('offline', scans.status(self.root)['message'])
        with patch.object(cli, '_run', side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):
                cli.cmd_run(self.args())
        self.assertFalse(scans.status(self.root)['running'])
        self.assertIn('test failure', scans.status(self.root)['message'])

    def test_http_start_runs_real_cli_in_isolated_database(self):
        # All sources and screening are disabled: test the real launch path without network or quota usage.
        store = Store(self.db)
        self.addCleanup(store.close)
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(store, self.config))
        self.addCleanup(httpd.server_close)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        url = f'http://127.0.0.1:{httpd.server_port}/api/scan'
        with urllib.request.urlopen(urllib.request.Request(url, data=b'{}', headers={'Content-Type': 'application/json'})) as response:
            self.assertTrue(json.load(response)['started'])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with urllib.request.urlopen(url) as response:
                state = json.load(response)
            if not state['running']:
                break
            time.sleep(.05)
        self.assertEqual(state['status'], 'complete', state)
        self.assertEqual(state['new_jobs'], 0)
        self.assertEqual(len(store.runs()), 1)
        self.assertTrue((self.root / 'scan.log').exists())
