import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from jobFilter import apply_engine, application_state, profile
from jobFilter.models import Job
from jobFilter.providers import CODEX
from jobFilter.store import Store


class ResumeVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.resumes = self.root / "resume"
        self.resumes.mkdir()
        self.patches = [patch.object(profile, "RESUME_DIR", self.resumes),
                        patch.object(profile, "RESUME_TEXT_PATH", self.root / "resume.txt"),
                        patch.object(profile, "SETTINGS_PATH", self.root / "settings.json"),
                        patch.object(profile, "resume_text", side_effect=lambda refresh=False, path=None: f"text of {(path or profile.resume_path()).name}"),
                        patch.object(profile, "load_profile", return_value={"name": "Alex Example"}),
                        patch.object(profile, "load_answers", return_value=[]),
                        patch.object(application_state, "APPLY_DIR", self.root / "apply"),
                        patch.object(apply_engine, "SKILL_PATH", self.root / "skill.md")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def pdf(self, name, age=0):
        path = self.resumes / name
        path.write_bytes(b"%PDF-1.4 test")
        os.utime(path, (time.time() - age,) * 2)
        return path

    def test_titles_pick_the_matching_version(self):
        mle = ["Junior Machine Learning Engineering", "Associate Engineer, Agentic AI", "AI Compiler Engineer New Grad",
               "Software Engineer, ML Infrastructure, Level 4", "2027 University Graduate - Machine Learning Engineer",
               "Applied AI Engineer 1", "Data Scientist, New Grad", "LLM Inference Engineer", "Research Engineer, NLP",
               "GenAI Platform Engineer", "Software Engineer, Computer Vision"]
        sde = ["Software Engineer New Grad - AI-Native", "Backend Software Engineer", "HTML Email Developer",
               "Data Engineer I", "Full Stack Developer", ""]
        for title in mle:
            with self.subTest(title=title):
                self.assertEqual(profile.job_resume({"title": title})[0], "mle")
        for title in sde:
            with self.subTest(title=title):
                self.assertEqual(profile.job_resume({"title": title}), ("sde", "title matches no other resume version"))
        self.assertEqual(profile.job_resume({"title": "Applied AI Engineer"})[1], 'title mentions "AI"')
        self.assertEqual(profile.job_resume({"title": "Backend Engineer"}, "mle")[0], "mle")
        self.assertEqual(profile.job_resume({"title": "ML Engineer"}, None)[0], "mle")
        with self.assertRaises(ValueError):
            profile.job_resume({"title": "ML Engineer"}, "designer")

    def test_file_names_mark_the_version(self):
        for name, version in (("Resume_Jason_Zhu_MLE_2026-09-23.pdf", "mle"), ("Resume-mle.pdf", "mle"),
                              ("Resume MLE.pdf", "mle"), ("Resume_Jason_Zhu_SDE_2026-08-12.pdf", "sde"),
                              ("ResumeMLE.pdf", "sde"), ("Resume_Simle.pdf", "sde"), ("resume.pdf", "sde")):
            with self.subTest(name=name):
                self.assertEqual(profile.file_version(Path(name)), version)

    def test_versions_fall_back_to_the_default_resume(self):
        self.assertIsNone(profile.resume_path("mle"))
        untagged = self.pdf("Resume_Jason_Zhu.pdf", age=200)
        self.assertEqual(profile.resume_path("mle"), untagged)
        self.assertIn("no *_MLE_* PDF", profile.application_resume({"title": "ML Engineer"})["reason"])
        sde = self.pdf("Resume_Jason_Zhu_SDE_2026-08-12.pdf", age=100)
        mle = self.pdf("Resume_Jason_Zhu_MLE_2026-09-23.pdf")
        # The MLE file is the newest PDF but never the default; untagged and SDE files compete by age.
        self.assertEqual(profile.resume_path(), sde)
        self.assertEqual(profile.resume_path("sde"), sde)
        chosen = profile.application_resume({"title": "ML Engineer"})
        self.assertEqual((chosen["version"], chosen["path"], chosen["text"]),
                         ("mle", mle, "text of Resume_Jason_Zhu_MLE_2026-09-23.pdf"))
        self.assertNotIn("default resume is used", chosen["reason"])
        self.assertEqual(profile.status()["resume_versions"], {"mle": str(mle)})
        sde.unlink()
        self.assertEqual(profile.status()["resume"], str(untagged))

    def test_reflow_keeps_titles_out_of_bullets_when_dates_have_their_own_line(self):
        raw = ("Backend Developer Intern, ByteDance, Seattle\nJun 2026 – Sep 2026\n"
               "• Owned backend development for the Braintree payment channel serving 40M+ daily requests across cards,\n"
               "Apple Pay, and Google Pay, driving architecture, feature delivery, and production operation that hot-\n"
               "swaps channels across the whole Payment Network, supporting cards, wallets, and local payment methods\n"
               "Backend Developer Intern, ByteDance, Shanghai\nApr 2025 – Sep 2025\n• Architected 3DS\n"
               "University of California, San Diego\nSep 2025 – Jun 2027 (Expected)\n")
        self.assertEqual(profile.reflow_resume(raw).splitlines(), [
            "Backend Developer Intern, ByteDance, Seattle", "Jun 2026 – Sep 2026",
            "• Owned backend development for the Braintree payment channel serving 40M+ daily requests across cards, "
            "Apple Pay, and Google Pay, driving architecture, feature delivery, and production operation that hot-swaps "
            "channels across the whole Payment Network, supporting cards, wallets, and local payment methods",
            "Backend Developer Intern, ByteDance, Shanghai", "Apr 2025 – Sep 2025", "• Architected 3DS",
            "University of California, San Diego", "Sep 2025 – Jun 2027 (Expected)"])

    def test_choice_is_saved_and_reaches_both_engines(self):
        self.pdf("Resume.pdf")
        mle = self.pdf("Resume_MLE.pdf")
        store = Store(self.root / "jobs.db")
        self.addCleanup(store.close)
        for job_id, title in (("ml", "Machine Learning Engineer"), ("backend", "Backend Engineer"), ("gpt", "AI Engineer")):
            job = Job.from_hit({"objectID": job_id})
            job.title, job.company, job.apply_url = title, "Example Test", f"https://example.com/{job_id}"
            store.upsert_many([job], "test")
        with self.assertRaises(ValueError):
            store.queue_application("backend", settings={"resume": "designer"})

        prompt = apply_engine.build_prompt(store.queue_application("ml"), self.root)
        self.assertIn(f"Resume file to upload: {mle}", prompt)
        self.assertIn('Resume version: MLE (title mentions "Machine Learning")', prompt)
        self.assertIn("text of Resume_MLE.pdf", prompt)

        app = store.queue_application("backend", settings={"resume": "mle"})
        self.assertEqual(app["settings"]["resume"], "mle")
        self.assertIn("Resume version: MLE (chosen when the application was started)", apply_engine.build_prompt(app, self.root))

        store.queue_application("gpt", CODEX, {"codex_model": "gpt-test", "resume": "sde"})
        task = application_state.claim_application(store, "gpt")
        self.assertEqual(Path(task["resume_path"]).name, "Resume.pdf")
        self.assertEqual(task["resume_version"], "SDE (chosen when the application was started)")
        with self.assertRaises(ValueError):
            application_state.claim_application(store, "missing")


if __name__ == "__main__":
    unittest.main()
