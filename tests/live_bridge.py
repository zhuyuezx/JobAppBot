"""Opt-in real ChatGPT + browser test. Fictional data; never uses the production queue."""
import argparse
import json
import os
import signal
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobFilter import application_state, apply_engine, bridge, profile, server
from jobFilter.models import Job
from jobFilter.store import Store
FORM = (Path(__file__).parent / "fixtures/application.html").read_bytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--bridge-port', type=int, default=8932)
    args = parser.parse_args()
    root = args.dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ.update(JOBFILTER_BRIDGE_DIR=str(root / 'browser'), JOBFILTER_BRIDGE_PORT=str(args.bridge_port), JOBFILTER_BRIDGE_HEADLESS='1')
    profile.PROFILE_DIR = profile.SETUP_DIR = root
    profile.PROFILE_PATH, profile.ANSWERS_PATH = root / 'profile.json', root / 'answers.json'
    profile.SETTINGS_PATH = root / 'settings.json'
    profile.RESUME_DIR, profile.RESUME_TEXT_PATH = root / 'resume', root / 'resume.txt'
    profile.COVER_LETTER_DIR = root / 'cover_letter'   # no template: no cover letter
    profile.ROLES_PATH = root / 'role_descriptions.json'
    application_state.APPLY_DIR = root / 'apply'
    profile.RESUME_DIR.mkdir()
    # A tiny valid PDF avoids adding a PDF-generation dependency to the test.
    stream = b'BT /F1 12 Tf 40 750 Td (Alex Example - Fictional test resume) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    pdf, offsets = b'%PDF-1.4\n', [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(pdf)); pdf += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(pdf)
    pdf += b'xref\n0 6\n0000000000 65535 f \n' + b''.join(f'{n:010d} 00000 n \n'.encode() for n in offsets[1:])
    pdf += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    (profile.RESUME_DIR / 'fictional-resume.pdf').write_bytes(pdf)
    profile.RESUME_TEXT_PATH.write_text('Alex Example. Fictional test resume.')
    profile.save_profile({'name': 'Alex Example', 'email': 'alex@example.com', 'needs_sponsorship': True})
    profile.save_settings({'engine': 'codex-playwright'})
    store = Store(root / 'jobs.db')
    job = Job.from_hit({'objectID': 'bridge-test'})
    job.title, job.company, job.apply_url = 'Graduate Engineer (test)', 'Fictional Employer', f'http://127.0.0.1:{args.port}/form'
    store.upsert_many([job], 'test')
    config = root / 'search.json'
    config.write_text(json.dumps({'screening': {'enabled': True}}))
    form = FORM.replace(b'<button type="button" id="review">', b'<label>Resume <input type="file" name="resume" accept=".pdf" required></label><button type="button" id="review">')
    form = form.replace(b'const values=Object.fromEntries(new FormData(form));', b'const values=Object.fromEntries(new FormData(form)); values.resume=values.resume.name;')
    loads = []
    class Handler(server.make_handler(store, config)):
        def do_GET(self):
            if self.path == '/form':
                loads.append(time.time())
                return self._send(200, form, 'text/html')
            super().do_GET()
        def do_POST(self):
            if self.path == '/fixture/review':
                (root / 'observed.json').write_text(json.dumps(self._body()))
                return self._json({'ok': True})
            if self.path == '/fixture/submit':
                (root / 'submitted').touch()
                return self._json({'ok': True})
            super().do_POST()
    httpd = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    def api(path, body=None):
        req = urllib.request.Request(f'http://127.0.0.1:{args.port}' + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as response:
            value = json.load(response)
        assert 'error' not in value, value
        return value
    def wait_status(expected):
        deadline = time.monotonic() + 950
        previous = None
        while time.monotonic() < deadline:
            app = api('/api/application?id=bridge-test')
            if app['status'] != previous:
                print('Application:', app['status'], flush=True); previous = app['status']
            if app['status'] == expected:
                return app
            if app['status'] not in ('queued', 'running'):
                raise AssertionError(app)
            time.sleep(1)
        raise AssertionError('Timed out waiting for ' + expected)
    try:
        print('Isolated UI:', f'http://127.0.0.1:{args.port}/#settings', flush=True)
        api('/api/applications/queue', {'job_id': 'bridge-test'})
        app = wait_status('needs_answer')
        questions = [q for q in app['questions'] if q['status'] == 'open']
        assert len(questions) == 1, questions
        assert 'date' in questions[0]['question'].lower(), questions
        api('/api/questions/answer', {'id': questions[0]['id'], 'answer': '2026-10-01', 'save': False})
        app = wait_status('review_ready')
        observed = json.loads((root / 'observed.json').read_text())
        expected = {'full_name': 'Alex Example', 'email': 'alex@example.com', 'sponsorship': 'Yes', 'start_date': '2026-10-01', 'resume': 'fictional-resume.pdf'}
        assert observed == expected, observed
        assert app['attempts'] == 2 and len(loads) == 1, (app['attempts'], loads)
        assert app['questions'][0]['status'] == 'sent'
        assert not profile.load_answers(), 'Answer once leaked into shared bank'
        assert not (root / 'submitted').exists(), 'Application was submitted'
        assert Path(app['screenshot']).stat().st_size > 0
        session = bridge.Session(bridge.endpoint())
        tabs = session.call('browser_tabs', {'action': 'list'})
        assert job.apply_url in json.dumps(tabs), tabs
        session.close()
        report = {'passed': True, 'status': app['status'], 'attempts': app['attempts'], 'page_loads': len(loads),
                  'observed': observed, 'submissions': 0, 'screenshot': app['screenshot'], 'tabs_survive_codex_exit': True}
        (root / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2), flush=True)
    finally:
        if apply_engine._worker:
            apply_engine._worker.stop_flag.set()
        state = root / 'browser/state.json'
        if state.exists():
            os.kill(json.loads(state.read_text())['pid'], signal.SIGTERM)
        httpd.shutdown(); httpd.server_close()
        if apply_engine._worker:
            apply_engine._worker.join(timeout=10)
        store.close()


if __name__ == '__main__':
    main()
