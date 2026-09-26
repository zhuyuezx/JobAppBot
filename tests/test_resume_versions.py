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
                        patch.object(profile, "ROLES_PATH", self.root / "role_descriptions.json"),
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


    def test_role_descriptions_keep_the_most_complete_wording_and_rebuild_only_on_change(self):
        self.assertEqual([(e["kind"], e["heading"], e["dates"], len(e["bullets"])) for e in profile.resume_entries(WORD_TEXT)],
                         [("experience", "Backend Developer Intern, ByteDance, Seattle", "Jun 2026 – Sep 2026", 4),
                          ("projects", "Main Developer & Maintainer, DiagWiki", "Dec 2025 – Present", 2)])
        self.assertEqual([(e["kind"], e["heading"], e["dates"], len(e["bullets"])) for e in profile.resume_entries(LATEX_TEXT)],
                         [("experience", "Backend Developer Intern, ByteDance, Seattle", "Jun 2026 – Sep 2026", 2),
                          ("projects", "Research Intern, DGP Lab", "Sep 2023 – Apr 2025", 1),
                          ("projects", "Main Developer & Maintainer, DiagWiki", "Dec 2025 – Present", 2)])

        texts = {"Resume_SDE.pdf": WORD_TEXT, "Resume_MLE.pdf": LATEX_TEXT}
        self.pdf("Resume_SDE.pdf", age=100)
        mle = self.pdf("Resume_MLE.pdf")
        with patch.object(profile, "resume_text", side_effect=lambda refresh=False, path=None: texts[path.name]) as read:
            entries = profile.role_descriptions()
            self.assertEqual([(e["heading"], e["from"], len(e["bullets"])) for e in entries],
                             [("Backend Developer Intern, ByteDance, Seattle", "sde", 4),   # the MLE PDF shows only 2
                              ("Main Developer & Maintainer, DiagWiki", "mle", 2),        # MLE wording is longer
                              ("Research Intern, DGP Lab", "mle", 1)])                    # only on the MLE resume
            calls = read.call_count
            self.assertEqual(profile.role_descriptions(), entries)
            self.assertEqual(read.call_count, calls, "stored: no resume is read again")
            texts["Resume_MLE.pdf"] = LATEX_TEXT.replace("• Built the MLE bullet.", "• Built the MLE bullet.\n• A new bullet.")
            os.utime(mle, (time.time() + 5,) * 2)   # the MLE resume changed
            profile.role_descriptions()
            self.assertGreater(read.call_count, calls)
        text = profile.role_descriptions_text()
        self.assertTrue(text.startswith("JOBS (for work experience entries)\nBackend Developer Intern, ByteDance, Seattle | Jun 2026 – Sep 2026\n• Owned"))
        self.assertIn("PROJECTS & RESEARCH\nMain Developer & Maintainer, DiagWiki", text)

    def test_mle_application_prompts_carry_the_complete_role_descriptions(self):
        texts = {"Resume.pdf": WORD_TEXT, "Resume_MLE.pdf": LATEX_TEXT}
        self.pdf("Resume.pdf", age=100)
        self.pdf("Resume_MLE.pdf")
        store = Store(self.root / "jobs.db")
        self.addCleanup(store.close)
        job = Job.from_hit({"objectID": "ml"})
        job.title, job.company, job.apply_url = "Machine Learning Engineer", "Example Test", "https://example.com/ml"
        store.upsert_many([job], "test")
        with patch.object(profile, "resume_text", side_effect=lambda refresh=False, path=None: texts[path.name]):
            prompt = apply_engine.build_prompt(store.queue_application("ml"), self.root)
            self.assertIn("===== RESUME TEXT (the uploaded MLE file) =====", prompt)
            roles = prompt.split("===== ROLE DESCRIPTIONS")[1]
            self.assertIn("• Drove resolution of an average of 2+ production incidents", roles)
            store.delete_application("ml")
            store.queue_application("ml", CODEX, {"codex_model": "gpt-test"})
            self.assertIn("• Drove resolution", application_state.claim_application(store, "ml")["role_descriptions"])


WORD_TEXT = """Yuezhexuan(Jason) Zhu
Education
University of California, San Diego  Sep 2025 – Jun 2027 (Expected)
Master of Science in Computer Science | GPA: 3.89 / 4.00
Internship
Backend Developer Intern, ByteDance, Seattle  Jun 2026 – Sep 2026
• Owned backend development for the Braintree payment channel serving 40M+ daily requests
• Designed and self-tested Braintree's migration to standardized interfaces
• Built an AI-agent E2E validation system from 1,615 test cases
• Drove resolution of an average of 2+ production incidents weekly across Braintree, Itaú, and Payoneer
Project & Research
Main Developer & Maintainer, DiagWiki  Dec 2025 – Present
• Developed a diagram-centered agentic AI tool
• Engineered a local RAG system"""

LATEX_TEXT = """Yuezhexuan (Jason) Zhu
Education
University of California, San Diego
Sep 2025 – Jun 2027 (Expected)
Technical Skills
Languages:
Python, C/C++
Experience
Backend Developer Intern, ByteDance, Seattle
Jun 2026 – Sep 2026
• Built an AI-agent E2E validation system from 1,615 test cases
• Owned backend development for the Braintree payment channel
Research & Projects
Research Intern, DGP Lab
Sep 2023 – Apr 2025
• Built the MLE bullet.
Main Developer & Maintainer, DiagWiki
Dec 2025 – Present
• Developed a fully local agentic AI tool that analyzes codebases and generates diagrams
• Engineered a hybrid RAG pipeline that fuses FAISS dense retrieval with BM25 scores"""


if __name__ == "__main__":
    unittest.main()
