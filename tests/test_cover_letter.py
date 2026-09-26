import io
import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import fitz

from jobFilter import apply_engine, application_state, bridge, bridge_engine, cover_letter, descriptions, profile
from jobFilter.models import Job
from jobFilter.store import Store

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
TEMPLATE = [
    ('<w:pPr><w:spacing w:after="0"/></w:pPr>', "Alex Example", True),
    ('<w:pPr><w:spacing w:after="280"/></w:pPr>', "(555) 010-0000 | alex@example.com", False),
    ("", "[Date]", False),
    ("", "Dear [Hiring Manager Name / Company Hiring Team],", False),
    ("", "I am writing to apply for the [Position Title] role at [Company]. I am pursuing an M.S. in Computer "
         "Science, and at ByteDance I ran 1,615 test cases across 32 channels.", False),
    ("", "What draws me to [Company] is [one or two specific reasons from the posting]. I would bring the same "
         "ownership to the team, from requirements to staged rollouts. Thank you for your time and consideration.", False),
    ('<w:pPr><w:spacing w:after="0"/></w:pPr>', "Sincerely,", False),
    ("", "Alex Example", False),
]


def docx(path, paragraphs=TEMPLATE):
    bold_on, bold_off = '<w:b/>', '<w:b w:val="0"/>'
    body = "".join(f'<w:p>{ppr}<w:r><w:rPr>{bold_on if bold else bold_off}</w:rPr>'
                   f'<w:t xml:space="preserve">{text}</w:t></w:r></w:p>' for ppr, text, bold in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<w:document {W}><w:body>{body}<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
                                        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>')
        z.writestr("word/styles.xml", f'<w:styles {W}><w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="24"/></w:rPr></w:rPrDefault>'
                                      '<w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="278" w:lineRule="auto"/></w:pPr>'
                                      '</w:pPrDefault></w:docDefaults></w:styles>')
    return path


GOOD = ["Dear Acme Corp Hiring Team,",
        "I am writing to apply for the Backend Engineer role at Acme Corp. I am pursuing an M.S. in Computer Science, "
        "and at ByteDance I ran 1,615 test cases across 32 channels.",
        "What draws me to Acme Corp is its reusable rockets. I would bring the same ownership to the team, from "
        "requirements to staged rollouts. Thank you for your time and consideration."]


class CoverLetterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.templates = self.root / "cover_letter"
        self.templates.mkdir()
        self.sde = docx(self.templates / "Cover_Letter_Template_SDE.docx")
        self.patches = [
            patch.object(profile, "COVER_LETTER_DIR", self.templates),
            patch.object(profile, "ROLES_PATH", self.root / "role_descriptions.json"),
            patch.object(profile, "RESUME_DIR", self.root / "resume"),
            patch.object(profile, "load_profile", return_value={"identity": {"preferred_name": "Alex Example"},
                                                                "preferences": {"earliest_start_date": "06-2027"}}),
            patch.object(profile, "load_answers", return_value=[]),
            patch.object(profile, "application_resume", side_effect=lambda job, choice=None: {
                "version": profile.job_resume(job, choice)[0], "reason": "test", "path": None,
                "text": "Built an AI-agent system from 1,615 test cases; served 40M+ daily requests."}),
            patch.object(descriptions, "fetch_description", return_value="Acme Corp builds reusable rockets. 3 days onsite."),
            patch.object(application_state, "APPLY_DIR", self.root / "apply"),
            patch.object(apply_engine, "SKILL_PATH", self.root / "skill.md"),
        ]
        for p in self.patches:
            p.start()
        self.app = {"job_id": "job-1", "engine": "claude-chrome", "settings": {"model": "opus"},
                    "job": {"title": "Backend Engineer", "company": "Acme Corp", "location": "Seattle, WA",
                            "apply_url": "https://example.com/job"}}

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_template_layout_comes_from_the_docx(self):
        tpl = cover_letter.read_template(self.sde)
        self.assertEqual((tpl["page"], tpl["margins"], tpl["size"]), ((612.0, 792.0), (72.0,) * 4, 12.0))
        self.assertEqual([p["after"] for p in tpl["paragraphs"]], [0.0, 14.0, 8.0, 8.0, 8.0, 8.0, 0.0, 8.0])
        self.assertEqual([p["bold"] for p in tpl["paragraphs"]][:2], [True, False])
        self.assertAlmostEqual(tpl["paragraphs"][0]["line"], 278 / 240)
        self.assertEqual([cover_letter.is_content(p["text"]) for p in tpl["paragraphs"]],
                         [False, False, True, True, True, True, False, False])

    def test_check_rejects_brackets_counts_and_unsupported_numbers(self):
        sources = "Built from 1,615 test cases; served 40M+ daily requests and 100K transactions."
        self.assertIsNone(cover_letter.check(["Ran 1615 tests, 40 million requests, 100,000 payments."], 1, sources))
        self.assertIn("exactly 2", cover_letter.check(["one"], 2, sources))
        self.assertIn("brackets", cover_letter.check(["At [Company]."], 1, sources))
        self.assertIn("5, 90", cover_letter.check(["Cut latency 90% in 5 weeks."], 1, sources))

    def test_letter_is_written_checked_rendered_and_reused(self):
        answers = [({"paragraphs": GOOD[:2] + ["What draws me to [Company] is rockets."], "notes": ""}, "opus"),
                   ({"paragraphs": GOOD, "notes": "Named the rocket program."}, "opus")]
        with patch.object(cover_letter, "_ask", side_effect=answers) as ask:
            letter = cover_letter.prepare(self.app, self.app["settings"])
        self.assertEqual(ask.call_count, 2)
        self.assertIn("no brackets may remain", ask.call_args_list[1].args[0])
        self.assertIn("Acme Corp builds reusable rockets", ask.call_args_list[0].args[0])
        self.assertFalse(ask.call_args_list[0].kwargs["fetch"])   # the description is in the prompt
        self.assertEqual(letter["status"], "ok", letter)
        pdf = Path(letter["path"])
        self.assertEqual(pdf.name, "Cover_Letter_Alex_Example_Acme_Corp.pdf")
        doc = fitz.open(pdf)
        text = doc[0].get_text()
        self.assertEqual(len(doc), 1)
        self.assertEqual(doc.metadata["author"], "Alex Example")
        for expected in ("Alex Example", time.strftime("%B %d, %Y").replace(" 0", " "), "Dear Acme Corp Hiring Team,",
                         "reusable rockets", "Sincerely,"):
            self.assertIn(expected, text)
        self.assertTrue(letter["text"].startswith("Dear Acme Corp Hiring Team,"))
        self.assertTrue(letter["text"].endswith("Sincerely,\n\nAlex Example"))
        self.assertEqual(cover_letter.load("job-1")["path"], str(pdf))

        with patch.object(cover_letter, "_ask") as ask:
            self.assertEqual(cover_letter.prepare(self.app, self.app["settings"])["path"], str(pdf))
            ask.assert_not_called()
            os.utime(self.sde, (time.time() + 5,) * 2)   # an edited template gets a new letter
            ask.return_value = ({"paragraphs": GOOD, "notes": ""}, "opus")
            self.assertEqual(cover_letter.prepare(self.app, self.app["settings"])["status"], "ok")
            ask.assert_called_once()

    def test_failures_never_stop_the_application(self):
        long = [GOOD[0]] + [" ".join([GOOD[1]] * 12)] * 2
        with patch.object(cover_letter, "_ask", return_value=({"paragraphs": long, "notes": ""}, "opus")) as ask:
            letter = cover_letter.prepare(self.app, self.app["settings"])
        self.assertEqual(ask.call_count, 2)
        self.assertIn("fits on one page", ask.call_args_list[1].args[0])
        self.assertEqual(letter["status"], "failed")
        self.assertFalse(list((self.root / "apply" / "job-1").glob("*.pdf")))
        with patch.object(cover_letter, "_ask", side_effect=RuntimeError("quota")):
            self.assertEqual(cover_letter.prepare(self.app, self.app["settings"]),
                             {**cover_letter.load("job-1"), "status": "failed", "reason": "quota"})
        self.sde.unlink()
        self.assertEqual(cover_letter.prepare(self.app, self.app["settings"])["status"], "skipped")

    def test_template_follows_the_resume_version(self):
        ml = {**self.app, "job": {**self.app["job"], "title": "Machine Learning Engineer"}}
        self.assertEqual(profile.cover_letter_template("mle"), self.sde)   # no MLE template yet
        mle = docx(self.templates / "Cover_Letter_Template_MLE.docx")
        (self.templates / "~$ver_Letter_Template_MLE.docx").write_bytes(b"Word lock file")
        with patch.object(cover_letter, "_ask", return_value=({"paragraphs": GOOD, "notes": ""}, "opus")):
            self.assertEqual(cover_letter.prepare(ml, ml["settings"])["template"], str(mle))
            self.assertEqual(cover_letter.prepare({**ml, "job_id": "job-2", "settings": {"resume": "sde"}}, {})["template"],
                             str(self.sde))

    def test_both_engines_hand_the_letter_to_the_browser_agent(self):
        letter = {"status": "ok", "path": "/tmp/Cover_Letter_Alex_Example_Acme_Corp.pdf", "text": "Dear Acme Corp Hiring Team,"}
        prompt = apply_engine.build_prompt(self.app, self.root, letter)
        self.assertIn("Cover letter file to upload: /tmp/Cover_Letter_Alex_Example_Acme_Corp.pdf", prompt)
        self.assertIn("===== COVER LETTER TEXT =====\nDear Acme Corp Hiring Team,", prompt)
        self.assertIn("Cover letter file to upload: (none: no template)",
                      apply_engine.build_prompt(self.app, self.root, {"status": "skipped", "reason": "no template"}))

        store = Store(self.root / "jobs.db")
        self.addCleanup(store.close)
        job = Job.from_hit({"objectID": "job-1"})
        job.title, job.company, job.apply_url = "Backend Engineer", "Acme Corp", "https://example.com/job"
        store.upsert_many([job], "test")
        app = store.queue_application("job-1", "codex-playwright", {"codex_model": "gpt-test"})
        seen = {}
        def fake(prompt, schema, *args, **kwargs):
            seen["prompt"] = prompt
            raise RuntimeError("stop after the prompt")
        with patch.object(cover_letter, "prepare", return_value=letter) as prepare, \
                patch.object(bridge, "ensure_running", return_value="http://127.0.0.1:8931/mcp"), \
                patch.object(bridge_engine, "run_codex", side_effect=fake):
            bridge_engine.run_application(store, app)
        prepare.assert_called_once()
        self.assertFalse(prepare.call_args.kwargs["write"], "a first run never pays for a letter")
        self.assertIn('"cover_letter_path": "/tmp/Cover_Letter_Alex_Example_Acme_Corp.pdf"', seen["prompt"])
        self.assertIn("to a cover letter field (required or optional)", seen["prompt"])


    def _store_with_job(self):
        store = Store(self.root / "jobs.db")
        self.addCleanup(store.close)
        job = Job.from_hit({"objectID": "job-1"})
        job.title, job.company, job.location, job.apply_url = "Backend Engineer", "Acme Corp", "Seattle, WA", "https://example.com/job"
        store.upsert_many([job], "test")
        return store

    def test_claude_writes_the_letter_only_when_the_form_asks(self):
        store, runs = self._store_with_job(), []
        def claude(results):
            def popen(cmd, **kwargs):
                runs.append(cmd)
                event = {"type": "result", "structured_output": results.pop(0), "session_id": "s1"}
                return Mock(stdout=io.StringIO(json.dumps(event) + "\n"), returncode=0)
            return popen
        with patch.object(apply_engine, "find_claude", return_value="claude"), \
                patch.object(cover_letter, "_ask", return_value=({"paragraphs": GOOD, "notes": ""}, "opus")) as ask:
            with patch.object(apply_engine.subprocess, "Popen", side_effect=claude([DONE])):
                self.assertEqual(apply_engine.run_application(store, store.queue_application("job-1"))["status"], "review_ready")
            ask.assert_not_called()   # no cover letter field, no letter
            self.assertIn("Cover letter file to upload: (not written yet", runs[0][2])
            self.assertEqual(cover_letter.load("job-1"), {})

            store.delete_application("job-1"); runs.clear()
            with patch.object(apply_engine.subprocess, "Popen", side_effect=claude([ASKING, DONE])):
                self.assertEqual(apply_engine.run_application(store, store.queue_application("job-1"))["status"], "review_ready")
            ask.assert_called_once()
            self.assertEqual(len(runs), 2)
            self.assertNotIn("--resume", runs[0])
            self.assertEqual(runs[1][runs[1].index("--resume") + 1], "s1", "the same session continues")
            letter = cover_letter.load("job-1")
            self.assertIn(f"JobAppBot wrote the cover letter for this job: {letter['path']}", runs[1][2])
            self.assertIn("===== COVER LETTER TEXT =====\nDear Acme Corp Hiring Team,", runs[1][2])

            store.delete_application("job-1"); runs.clear()   # the letter now exists and is handed over up front
            with patch.object(apply_engine.subprocess, "Popen", side_effect=claude([ASKING])):
                self.assertEqual(apply_engine.run_application(store, store.queue_application("job-1"))["status"], "failed")
            self.assertEqual(len(runs), 1, "asking again after being answered does not loop")
            self.assertIn(f"Cover letter file to upload: {letter['path']}", runs[0][2])
            ask.assert_called_once()

    def test_codex_writes_the_letter_only_when_the_form_asks(self):
        store, prompts = self._store_with_job(), []
        def codex(prompt, schema, *args, **kwargs):
            prompts.append(prompt)
            if len(prompts) == 1:
                return ASKING, {"model": "codex/test"}
            shot = application_state.work_directory("job-1") / (store.get_application("job-1")["session_id"] + ".png")
            shot.write_bytes(b"screenshot")
            return {**DONE, "screenshot_path": str(shot)}, {"model": "codex/test"}
        app = store.queue_application("job-1", "codex-playwright", {"codex_model": "gpt-test"})
        with patch.object(bridge, "ensure_running", return_value="http://127.0.0.1:8931/mcp"), \
                patch.object(bridge_engine, "run_codex", side_effect=codex), \
                patch.object(cover_letter, "_ask", return_value=({"paragraphs": GOOD, "notes": ""}, "opus")) as ask:
            self.assertEqual(bridge_engine.run_application(store, app)["status"], "review_ready")
        ask.assert_called_once()
        self.assertIn('"cover_letter_status": "not written yet"', prompts[0])
        self.assertIn('"cover_letter_status": "ready"', prompts[1])
        self.assertIn(f'"cover_letter_path": "{cover_letter.load("job-1")["path"]}"', prompts[1])


ASKING = {"status": "needs_cover_letter", "summary": "Optional Cover Letter upload on the Greenhouse form.",
          "page_url": "https://example.com/job", "unanswered_questions": []}
DONE = {"status": "review_ready", "summary": "Filled; not submitted.", "page_url": "https://example.com/job",
        "filled_fields": ["Cover letter: Cover_Letter_Alex_Example_Acme_Corp.pdf"], "unanswered_questions": []}


if __name__ == "__main__":
    unittest.main()
