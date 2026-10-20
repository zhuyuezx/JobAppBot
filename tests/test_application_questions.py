import tempfile
import unittest
from pathlib import Path

from jobFilter import apply_engine
from jobFilter.models import Job
from jobFilter.store import Store


class ApplicationQuestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "test.db")
        for job_id in ("one", "two"):
            job = Job.from_hit({"objectID": job_id})
            job.title, job.company, job.apply_url = job_id, job_id, f"https://example.com/{job_id}"
            self.store.upsert_many([job], "test")
            self.store.queue_application(job_id)
            self.store.update_application(job_id, status="needs_answer")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_repeated_prompt_is_one_question_but_different_choices_and_jobs_stay_separate(self):
        first = self.store.add_question("one", "Start date?")
        self.assertEqual(first, self.store.add_question("one", " START  date ? "))
        self.assertNotEqual(first, self.store.add_question("two", "Start date?"))
        self.assertNotEqual(first, self.store.add_question("one", "Start date?", ["Today", "Tomorrow"]))
        self.assertEqual(len(self.store.questions(job_id="one", status="open")), 2)

    def test_result_replacement_keeps_ids_and_deduplicates_both_provider_results(self):
        first = self.store.add_question("one", "Start date?")
        old = self.store.add_question("one", "Obsolete question")
        with self.store.conn:
            self.store.replace_open_questions("one", [
                {"question": "Start date?"}, {"question": "START DATE?"},
                {"question": "Relocate?", "options": ["Yes", "No"]},
                {"question": "Relocate?", "options": ["No", "Yes"]},
            ])
        questions = self.store.questions(job_id="one", status="open")
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0]["id"], first)
        self.assertEqual(next(q for q in self.store.questions() if q["id"] == old)["status"], "superseded")

    def test_legacy_duplicates_are_hidden_and_answered_together_without_early_resume(self):
        first = self.store.add_question("one", "Start date?")
        # Simulate duplicates saved before this fix.
        with self.store.conn:
            self.store.conn.execute(
                "INSERT INTO questions (job_id, question, created_at) VALUES ('one', 'START DATE ?', 'now')")
        second = self.store.add_question("one", "Relocate?")
        self.assertEqual(len(self.store.questions(job_id="one", status="open")), 2)
        self.store.answer_question(second, "Yes")
        self.assertFalse(apply_engine.requeue_if_answered(self.store, "one"))
        self.assertEqual(len(self.store.questions(job_id="one", status="open")), 1)
        self.store.answer_question(first, "March")
        self.assertTrue(apply_engine.requeue_if_answered(self.store, "one"))
        self.assertEqual(len(self.store.questions(job_id="one", status="open")), 0)
        answers = self.store.questions(job_id="one", status="answered")
        self.assertEqual([q["answer"] for q in answers], ["March", "March", "Yes"])

    def test_result_question_changes_roll_back_with_failed_result(self):
        first = self.store.add_question("one", "Start date?")
        with self.assertRaises(RuntimeError):
            with self.store.conn:
                self.store.replace_open_questions("one", [{"question": "Different question"}])
                raise RuntimeError("failed result")
        self.assertEqual([q["id"] for q in self.store.questions(status="open")], [first])
