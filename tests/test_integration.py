import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobFilter import apply_engine, bridge, bridge_engine, codex, application_state, profile, screen
from jobFilter.models import Job
from jobFilter.store import Store


def add_job(store, name="test", url="https://example.com/job"):
    job = Job.from_hit({"objectID": name})
    job.title, job.company, job.apply_url = "Graduate Engineer", "Example Test", url
    store.upsert_many([job], "test")
    return store.get(name)


def result(status="review_ready"):
    return {"status": status, "summary": "Test form checked; not submitted.", "page_url": "https://example.com/job",
            "filled_fields": ["Name: Alex Example"], "unanswered_questions": []}


VERDICT = {"statement": "no_sponsorship", "requires_citizenship": False, "company_verdict": "unknown",
           "company_evidence": [], "verdict": "unlikely", "new_grad_fit": True, "fit_score": 2,
           "summary": "No sponsorship.", "evidence": ["We cannot sponsor visas."], "sources": []}


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "jobs.db")
        self.patches = [patch.object(profile, "PROFILE_PATH", self.root / "profile.json"),
                        patch.object(profile, "PROFILE_DIR", self.root),
                        patch.object(profile, "SETTINGS_PATH", self.root / "settings.json"),
                        patch.object(profile, "load_profile", return_value={"name": "Alex Example"}),
                        patch.object(profile, "load_answers", return_value=[]),
                        patch.object(profile, "resume_path", return_value=None),
                        patch.object(profile, "resume_text", return_value=""),
                        patch.object(application_state, "APPLY_DIR", self.root / "apply"),
                        patch.object(apply_engine, "SKILL_PATH", self.root / "skill.md")]
        for p in self.patches:
            p.start()
        add_job(self.store)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.store.close()
        self.tmp.cleanup()

    def test_defaults_and_provider_settings(self):
        self.assertEqual(profile.load_settings()["engine"], "claude-chrome")
        self.assertEqual(screen.screening_config({})["provider"], "claude")
        profile.save_settings({"engine": "codex-playwright"})
        self.assertEqual(profile.load_settings()["model"], "opus")
        with self.assertRaises(ValueError):
            profile.save_settings({"engine": "invalid"})
        with self.assertRaises(ValueError):
            screen.screening_config({"screening": {"provider": "invalid"}})

    def test_frontend_screening_settings_preserve_search_and_claude_model(self):
        path = self.root / "search.json"
        original = {"search_state": {"searchQuery": "software engineer"}, "rules": {"max_min_yoe": 1},
                    "screening": {"model": "haiku", "max_per_run": 7, "_note": "keep this"}}
        path.write_text(json.dumps(original))
        saved = screen.save_screening_settings(path, {"provider": "codex", "codex_model": "", "enabled": True, "rules": {}})
        current = json.loads(path.read_text())
        self.assertEqual(saved["provider"], "codex")
        self.assertEqual(saved["model"], "haiku")
        self.assertEqual(current["search_state"], original["search_state"])
        self.assertEqual(current["rules"], original["rules"])
        self.assertEqual(current["screening"]["max_per_run"], 7)
        self.assertEqual(current["screening"]["_note"], "keep this")
        before = path.read_text()
        for update in ({"provider": "invalid"}, {"enabled": "yes"}, {"codex_model": []}):
            with self.assertRaises(ValueError):
                screen.save_screening_settings(path, update)
            self.assertEqual(path.read_text(), before)
        self.assertEqual(screen.save_screening_settings(path, {"provider": "claude"})["model"], "haiku")

    def test_queues_are_separate_and_engine_cannot_change(self):
        app = self.store.queue_application("test", "codex-playwright")
        self.assertIsNone(self.store.next_queued("claude-chrome"))
        self.assertIsNotNone(self.store.next_queued("codex-playwright"))
        with self.assertRaises(ValueError):
            self.store.queue_application("test", "claude-chrome")
        self.assertEqual(self.store.get_application("test")["status"], "queued")

    def test_application_question_resume_one_time_answer(self):
        self.store.queue_application("test", "codex-playwright")
        task = application_state.claim_application(self.store, "test")
        with self.assertRaises(ValueError):
            application_state.claim_application(self.store, "test")
        out = result("needs_answer")
        out["unanswered_questions"] = [{"question": "Start date?", "options": []}]
        application_state.complete_application(self.store, "test", task["claim_token"], out)
        q = self.store.questions(job_id="test")[0]
        self.store.answer_question(q["id"], "2026-10-01")
        self.assertTrue(apply_engine.requeue_if_answered(self.store, "test"))
        resumed = application_state.claim_application(self.store, "test")
        self.assertEqual(resumed["questions"][0]["answer"], "2026-10-01")
        self.assertEqual(resumed["page_url"], out["page_url"])
        with self.assertRaises(ValueError):
            application_state.complete_application(self.store, "test", task["claim_token"], result())
        application_state.complete_application(self.store, "test", resumed["claim_token"], result())
        self.assertEqual(self.store.get_application("test")["status"], "review_ready")
        self.assertEqual(self.store.questions(job_id="test")[0]["status"], "sent")

    def test_cancelled_application_cannot_overwrite(self):
        self.store.queue_application("test", "codex-playwright")
        task = application_state.claim_application(self.store, "test")
        self.store.update_application("test", status="skipped")
        with self.assertRaises(ValueError):
            application_state.complete_application(self.store, "test", task["claim_token"], result())
        self.assertEqual(self.store.get_application("test")["status"], "skipped")

    def test_invalid_result_does_not_change_state(self):
        self.store.queue_application("test", "codex-playwright")
        task = application_state.claim_application(self.store, "test")
        for out in [result("needs_answer"), {**result(), "filled_fields": []}, {**result(), "screenshot_path": "/tmp/old.png"}]:
            with self.assertRaises(ValueError):
                application_state.complete_application(self.store, "test", task["claim_token"], out)
        self.assertEqual(self.store.get_application("test")["status"], "running")

    def test_claude_command_and_resume_preserved(self):
        app = self.store.queue_application("test")
        event = {"type": "result", "structured_output": result(), "session_id": "claude-session"}
        proc = unittest.mock.Mock(stdout=io.StringIO(json.dumps(event) + "\n"), returncode=0)
        with patch.object(apply_engine, "find_claude", return_value="claude"), patch.object(apply_engine.subprocess, "Popen", return_value=proc) as run:
            self.assertEqual(apply_engine.run_application(self.store, app)["status"], "review_ready")
            cmd = run.call_args.args[0]
            for flag in ["--chrome", "--json-schema", "--allowedTools"]:
                self.assertIn(flag, cmd)
            self.assertNotIn("--resume", cmd)
            self.store.update_application("test", result=result("needs_login"))
            app = self.store.get_application("test")
            proc.stdout = io.StringIO(json.dumps(event) + "\n")
            apply_engine.run_application(self.store, app)
            self.assertIn("--resume", run.call_args.args[0])
            self.assertIn("claude-session", run.call_args.args[0])

    def test_screening_routes_and_model_labels(self):
        for provider in ("claude", "codex"):
            cfg = screen.screening_config({"screening": {"provider": provider}})
            with patch.object(screen, "fetch_description", return_value="We cannot sponsor visas."), patch.object(screen, "run_claude", return_value=(VERDICT, {})) as cc, patch.object(screen, "run_codex", return_value=(VERDICT, {})) as gpt:
                out = screen.screen_job(self.store, self.store.get("test"), cfg)
                self.assertEqual(out["status"], "ok")
                self.assertEqual(out["model"], "sonnet" if provider == "claude" else "codex/default")
                self.assertEqual(gpt.call_count, int(provider == "codex"))
                self.assertEqual(cc.call_count, int(provider == "claude"))

    def test_quota_leaves_jobs_unscreened_and_stops_batch(self):
        add_job(self.store, "second", "https://example.com/second")
        cfg = screen.screening_config({"screening": {"provider": "codex", "concurrency": 1}})
        with patch.object(screen, "fetch_description", return_value="Posting"), patch.object(screen, "run_codex", side_effect=codex.CodexUnavailable("usage limit")) as run:
            counts = screen.screen_batch(self.store, self.store.query(), cfg)
        self.assertEqual(counts["skipped"], 2)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(len(self.store.unscreened()), 2)

    def test_codex_runner_subscription_schema_stdin(self):
        original = json.loads(json.dumps(screen.SCHEMA))
        def fake(cmd, **kwargs):
            self.assertEqual(kwargs["input"], "test prompt")
            self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
            self.assertIn('forced_login_method="chatgpt"', cmd)
            schema = json.loads(Path(cmd[cmd.index("--output-schema") + 1]).read_text())
            self.assertFalse(schema["additionalProperties"])
            Path(cmd[cmd.index("--output-last-message") + 1]).write_text(json.dumps(VERDICT))
            return subprocess.CompletedProcess(cmd, 0, '{"type":"turn.completed","usage":{"output_tokens":42}}', '')
        with patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-use"}), patch.object(codex, "find_codex", return_value="codex"), patch.object(codex.subprocess, "run", side_effect=fake):
            out, meta = codex.run_codex("test prompt", screen.SCHEMA)
        self.assertEqual(out, VERDICT)
        self.assertIsNone(meta["total_cost_usd"])
        self.assertEqual(screen.SCHEMA, original)

    def test_codex_runner_reports_quota(self):
        proc = subprocess.CompletedProcess([], 1, '{"type":"turn.failed","error":{"message":"Usage limit reached"}}', '')
        with patch.object(codex, "find_codex", return_value="codex"), patch.object(codex.subprocess, "run", return_value=proc):
            with self.assertRaises(codex.CodexUnavailable):
                codex.run_codex("test", screen.SCHEMA)

    def test_automatic_queue_and_models_preserve_existing_providers(self):
        self.store.queue_application('test', 'codex-playwright')
        self.assertEqual(self.store.next_queued()['job_id'], 'test')
        self.assertIsNone(self.store.next_queued('claude-chrome'))
        add_job(self.store, 'claude', 'https://example.com/claude')
        self.store.queue_application('claude', 'claude-chrome')
        self.assertEqual(self.store.next_queued('claude-chrome')['job_id'], 'claude')
        profile.save_settings({'engine': 'codex-playwright', 'codex_model': 'gpt-test', 'codex_timeout': 600})
        saved = profile.save_settings({'engine': 'claude-chrome'})
        self.assertEqual((saved['model'], saved['max_turns'], saved['codex_model'], saved['codex_timeout']), ('opus', 120, 'gpt-test', 600))

    def test_bridge_dispatch_handoff_and_resume(self):
        app = self.store.queue_application('test', 'codex-playwright')
        def fake(prompt, schema, *args, **kwargs):
            self.assertEqual(kwargs['browser_url'], 'http://127.0.0.1:8931/mcp')
            self.assertFalse(kwargs['cancelled']())
            current = self.store.get_application('test')
            path = application_state.work_directory('test') / (current['session_id'] + '.png')
            path.write_bytes(b'test screenshot')
            out = result('needs_answer' if current['attempts'] == 1 else 'review_ready')
            out['screenshot_path'] = str(path)
            if current['attempts'] == 1:
                out['unanswered_questions'] = [{'question': 'Start date?', 'options': []}]
            else:
                self.assertIn('2026-10-01', prompt)
            return out, {'model': 'codex/test'}
        with patch.object(bridge, 'ensure_running', return_value='http://127.0.0.1:8931/mcp'), patch.object(bridge_engine, 'run_codex', side_effect=fake):
            self.assertEqual(apply_engine.run_application(self.store, app)['status'], 'needs_answer')
            q = self.store.questions(job_id='test')[0]
            self.store.answer_question(q['id'], '2026-10-01')
            self.assertTrue(apply_engine.requeue_if_answered(self.store, 'test'))
            self.assertEqual(apply_engine.run_application(self.store, self.store.get_application('test'))['status'], 'review_ready')
        self.assertEqual(self.store.questions(job_id='test')[0]['status'], 'sent')

    def test_bridge_requires_screenshot_and_preserves_cancellation(self):
        for cancel in (False, True):
            app = self.store.queue_application('test', 'codex-playwright')
            def fake(*args, **kwargs):
                if cancel:
                    self.store.update_application('test', status='skipped')
                    raise codex.CodexCancelled()
                return result(), {'model': 'codex/test'}
            with patch.object(bridge, 'ensure_running', return_value='http://127.0.0.1:8931/mcp'), patch.object(bridge_engine, 'run_codex', side_effect=fake):
                apply_engine.run_application(self.store, app)
            self.assertEqual(self.store.get_application('test')['status'], 'skipped' if cancel else 'failed')

    def test_browser_process_cancellation_and_timeout(self):
        real_popen = subprocess.Popen
        for cancelled, timeout, error in [(lambda: True, 30, codex.CodexCancelled), (lambda: False, .01, TimeoutError)]:
            processes = []
            def start(*args, **kwargs):
                proc = real_popen(*args, **kwargs); processes.append(proc); return proc
            with patch.object(codex.subprocess, 'Popen', side_effect=start), self.assertRaises(error):
                codex._browser_process([sys.executable, '-c', 'import sys,time; sys.stdin.read(); time.sleep(30)'], '', os.environ.copy(), timeout, lambda _: None, cancelled)
            self.assertIsNotNone(processes[0].poll(), 'Cancelled/timed-out child leaked')

    def test_bridge_missing_install_does_not_start_process(self):
        with patch.object(bridge, 'status', return_value={'running': False, 'installed': False}), patch.object(bridge.subprocess, 'Popen') as run:
            with self.assertRaisesRegex(RuntimeError, 'npm install'):
                bridge.ensure_running()
            run.assert_not_called()

    def test_browser_codex_configuration_is_scoped_to_run(self):
        def fake(cmd, prompt, env, timeout, log, cancelled):
            self.assertIn('mcp_servers.jobfilter_browser.required=true', cmd)
            self.assertIn('web_search="disabled"', cmd)
            self.assertIn('--ignore-user-config', cmd)
            self.assertIn('forced_login_method="chatgpt"', cmd)
            self.assertNotIn('OPENAI_API_KEY', env)
            Path(cmd[cmd.index('--output-last-message') + 1]).write_text(json.dumps(result()))
            return subprocess.CompletedProcess(cmd, 0, '', '')
        with patch.object(codex, 'find_codex', return_value='codex'), patch.object(codex, '_browser_process', side_effect=fake):
            out, _ = codex.run_codex('test', application_state.RESULT_SCHEMA, browser_url='http://127.0.0.1:8931/mcp')
            self.assertEqual(out['status'], 'review_ready')
            with self.assertRaises(ValueError):
                codex.run_codex('test', application_state.RESULT_SCHEMA, browser_url='https://example.com/mcp')

    def test_legacy_migration_preserves_history_and_parks_pending_work(self):
        for status in ('queued', 'running', 'needs_answer', 'review_ready', 'submitted'):
            add_job(self.store, status, 'https://example.com/' + status)
            self.store.queue_application(status, 'codex-playwright')
            self.store.update_application(status, status=status, session_id='old-claim',
                                          summary='old summary', page_url='https://example.com/form', result=result())
            self.store.add_question(status, 'Start date?')
        self.store.queue_application('test', 'claude-chrome')
        claude_before = self.store.get_application('test')
        with self.store.conn:
            self.store.conn.execute("UPDATE applications SET engine='codex-chrome' WHERE job_id != 'test'")
        migrated = Store(self.store.path)
        try:
            for status in ('queued', 'running', 'needs_answer', 'review_ready', 'submitted'):
                app = migrated.get_application(status)
                self.assertEqual(app['engine'], 'codex-playwright')
                self.assertIsNone(app['session_id'])
                self.assertEqual(app['status'], 'failed' if status in ('queued', 'running') else status)
                self.assertEqual(app['result'], result())
                self.assertEqual(len(migrated.questions(job_id=status)), 1)
                if status in ('queued', 'running'):
                    self.assertIn('Run again', app['summary'])
            self.assertEqual(migrated.get_application('test'), claude_before)
            before = migrated.list_applications()
            migrated._migrate()
            self.assertEqual(migrated.list_applications(), before, 'Migration must be idempotent')
            migrated.queue_application('queued', 'codex-playwright')
            task = application_state.claim_application(migrated, 'queued')
            self.assertEqual(task['previous_result'], result())
            self.assertNotEqual(task['claim_token'], 'old-claim')
        finally:
            migrated.close()

    def test_manual_provider_removed_and_old_settings_upgrade(self):
        profile.SETTINGS_PATH.write_text(json.dumps({'engine': 'codex-chrome', 'model': 'haiku', 'max_turns': 50}))
        settings = profile.load_settings()
        self.assertEqual(settings['engine'], 'codex-playwright')
        self.assertEqual(settings['model'], 'haiku')
        self.assertEqual(profile.save_settings({})['engine'], 'codex-playwright')
        with self.assertRaises(ValueError):
            profile.save_settings({'engine': 'codex-chrome'})
        with self.assertRaises(ValueError):
            self.store.queue_application('test', 'codex-chrome')
        from jobFilter.cli import build_parser
        parser = build_parser()
        with patch('sys.stderr', new=io.StringIO()):
            for args in (['apply', 'desktop-next'], ['apply', 'desktop-complete'], ['apply', 'queue', 'test', '--engine', 'codex-chrome']):
                with self.assertRaises(SystemExit):
                    parser.parse_args(args)
        self.assertEqual(parser.parse_args(['apply', 'queue', 'test', '--engine', 'codex-playwright']).engine, 'codex-playwright')


if __name__ == "__main__":
    unittest.main()
