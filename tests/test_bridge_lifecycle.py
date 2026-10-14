"""Run real browser checks with JOBFILTER_TEST_BROWSER=1; no LLM or live job forms."""
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from jobFilter import bridge


class OwnerTests(unittest.TestCase):
    def test_new_owner_acquires_browser_before_old_owner_is_released(self):
        order = []
        old, new = Mock(), Mock()
        new.call.side_effect = lambda *args: order.append('acquire')
        old.close.side_effect = lambda: order.append('release')
        with patch.object(bridge, 'Session', return_value=new):
            self.assertIs(bridge.renew_owner(old), new)
        self.assertEqual(order, ['acquire', 'release'])

    def test_failed_replacement_preserves_old_owner(self):
        old, new = Mock(), Mock()
        new.call.side_effect = RuntimeError('cannot acquire')
        with patch.object(bridge, 'Session', return_value=new), self.assertRaisesRegex(RuntimeError, 'cannot acquire'):
            bridge.renew_owner(old)
        old.close.assert_not_called()
        new.close.assert_called_once()

    def test_existing_daemon_must_retain_browser_before_returning_to_worker(self):
        with patch.object(bridge, 'status', return_value={'running': True, 'url': 'retained'}), \
             patch.object(bridge, 'retain_browser') as retain:
            self.assertEqual(bridge.ensure_running(), 'retained')
            retain.assert_called_once()
            retain.side_effect = RuntimeError('owner unavailable')
            with self.assertRaisesRegex(RuntimeError, 'owner unavailable'):
                bridge.ensure_running()

    def test_request_errors_are_reported_and_old_owner_is_kept(self):
        with tempfile.TemporaryDirectory() as d, patch.object(bridge, 'directory', return_value=Path(d)):
            requests = Path(d) / 'owner-requests'; requests.mkdir()
            ticket = requests / 'test.request'; ticket.touch()
            old = Mock()
            with patch.object(bridge, 'renew_owner', side_effect=RuntimeError('failed')):
                self.assertIs(bridge.service_owner_requests(old), old)
            self.assertEqual(json.loads(ticket.with_suffix('.reply').read_text()), {'error': 'failed'})
            self.assertFalse(ticket.exists())


@unittest.skipUnless(os.environ.get('JOBFILTER_TEST_BROWSER') == '1' and os.name != 'nt',
                     'opt-in isolated Playwright browser check')
class BrowserLifecycleTests(unittest.TestCase):
    def test_drafts_survive_agent_exit_after_browser_restart_and_repeated_runs(self):
        with tempfile.TemporaryDirectory(prefix='jobfilter-browser-test-') as d:
            root = Path(d)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            env = {**os.environ, 'JOBFILTER_BRIDGE_DIR': d, 'JOBFILTER_BRIDGE_PORT': str(port),
                   'JOBFILTER_BRIDGE_HEADLESS': '1'}
            with (root / 'test.log').open('w+') as log, patch.dict(os.environ, env):
                daemon = subprocess.Popen([sys.executable, '-m', 'jobFilter.bridge'], cwd=bridge.ROOT,
                                          env=env, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 25
                    while not (root / 'state.json').exists():
                        if daemon.poll() is not None or time.monotonic() > deadline:
                            log.seek(0); self.fail(log.read())
                        time.sleep(.1)
                    # Simulate quitting the initial window while its bridge stays alive.
                    browser_pid = int(os.readlink(root / 'profile/SingletonLock').rsplit('-', 1)[1])
                    os.kill(browser_pid, signal.SIGTERM)
                    deadline = time.monotonic() + 10
                    while True:
                        try:
                            os.kill(browser_pid, 0)
                        except ProcessLookupError:
                            break
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(.1)
                    for attempt in range(3):
                        bridge.ensure_running()
                        worker = bridge.Session(bridge.endpoint())
                        try:
                            worker.call('browser_tabs', {'action': 'new'})
                            worker.call('browser_evaluate', {'function':
                                f"() => {{ document.title='Draft {attempt}'; document.body.innerHTML='<input id=answer>'; "
                                f"document.querySelector('#answer').value='unsaved answer {attempt}'; }}"})
                        finally:
                            worker.close()  # Same MCP disconnect as a finished Codex run.
                        reviewer = bridge.Session(bridge.endpoint())
                        try:
                            tabs = json.dumps(reviewer.call('browser_tabs', {'action': 'list'}))
                            for previous in range(attempt + 1):
                                self.assertIn(f'Draft {previous}', tabs)
                                reviewer.call('browser_tabs', {'action': 'select', 'index': previous + 1})
                                result = reviewer.call('browser_evaluate', {'function': "() => document.querySelector('#answer')?.value"})
                                self.assertIn(f'unsaved answer {previous}', json.dumps(result))
                        finally:
                            reviewer.close()
                finally:
                    daemon.terminate()
                    try:
                        daemon.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        daemon.kill(); daemon.wait()
