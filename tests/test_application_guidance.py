import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobFilter import application_guidance as guidance, apply_engine, profile


class GuidanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.skill = self.root / 'apply-job' / 'SKILL.md'
        self.skill.parent.mkdir()

    def test_legacy_archive_is_lossless_and_migration_idempotent(self):
        legacy = '---\nname: apply-job\n---\nCustom old workflow\n## Learned from runs\n' + 'An old lesson.\n' * 20000
        self.skill.write_text(legacy)
        archive = guidance.sync_skill(self.skill)
        self.assertIn(legacy, archive.read_text())
        self.assertLess(len(self.skill.read_text()), 10000)
        first = archive.read_bytes()
        for _ in range(3):
            self.assertEqual(guidance.sync_skill(self.skill), archive)
        self.assertEqual(archive.read_bytes(), first)
        self.assertNotIn('An old lesson.', self.skill.read_text())
        self.assertIn(str(archive), self.skill.read_text())
        # User edits are preserved before resynchronizing the managed entrypoint.
        self.skill.write_text('An additional local correction.')
        guidance.sync_skill(self.skill)
        self.assertIn('An additional local correction.', archive.read_text())
        self.assertIn(legacy, archive.read_text())

    def test_new_lessons_do_not_grow_core_and_concurrent_writes_are_kept(self):
        self.skill.write_text('# Original workflow\n')
        guidance.sync_skill(self.skill)
        compact = self.skill.read_bytes()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            counts = list(pool.map(lambda i: guidance.append_lessons(self.skill, [f'Unique control quirk number {i}.'], 'Example'), range(8)))
        self.assertEqual(sum(counts), 8)
        self.assertEqual(self.skill.read_bytes(), compact)
        archive = guidance.archive_path(self.skill).read_text()
        for i in range(8):
            self.assertEqual(archive.count(f'Unique control quirk number {i}.'), 1)
        self.assertEqual(guidance.append_lessons(self.skill, ['Unique control quirk number 0.', '  Unique   control quirk number 0.  '], 'Example'), 0)
        self.assertEqual(self.skill.read_bytes(), compact)

    def test_prompt_size_is_independent_of_history_and_applicant_facts_are_not_truncated(self):
        self.skill.write_text('# Prior rules\n## Learned from runs\n' + 'IRRELEVANT_HISTORY ' * 30000)
        applicant = {'education': [{'school': f'School {i}', 'degree': 'Degree', 'end':'2027-06'} for i in range(4)],
                     'preferences': {'earliest_start_date':'2027-03-22'}}
        resume = 'resume facts ' * 900 + 'FINAL_RESUME_RECORD'
        roles = 'role description ' * 900 + 'FINAL_EMPLOYMENT_RECORD'
        with patch.object(apply_engine, 'SKILL_PATH', self.skill), \
             patch.object(profile, 'load_profile', return_value=applicant), \
             patch.object(profile, 'load_answers', return_value=[{'question':'Question', 'answer':'Known answer'}]), \
             patch.object(profile, 'application_resume', return_value={'path':'resume.pdf', 'version':'sde', 'reason':'test', 'text':resume}), \
             patch.object(profile, 'role_descriptions_text', return_value=roles):
            app = {'job': {'title':'Engineer', 'company':'Example', 'apply_url':'https://example.com/job'}}
            prompt = apply_engine.build_prompt(app, self.root)
            self.assertEqual(prompt.count(guidance.review_instructions()), 1)
            self.assertNotIn('IRRELEVANT_HISTORY', prompt)
            self.assertIn(resume, prompt)
            self.assertIn(roles, prompt)
            for record in applicant['education']:
                self.assertIn(record['school'], prompt)
            self.assertIn('Known answer', prompt)
            self.assertIn('2027-03-22', prompt)
            self.assertLess(len(prompt) - len(resume) - len(roles), 12000)
            guidance.append_lessons(self.skill, ['New unrelated site rule ' * 200], 'Unrelated')
            self.assertEqual(apply_engine.build_prompt(app, self.root), prompt)

    def test_guides_are_available_for_redirects_but_not_all_embedded(self):
        text = guidance.reference_instructions(self.skill)
        for name in guidance.GUIDE_LABELS:
            path = guidance.GUIDES / (name + '.md')
            self.assertTrue(path.exists())
            self.assertIn(str(path.resolve()), text)
            self.assertNotIn(path.read_text(), text)
        self.assertLess(len(text), 2200)
        # A brand-new installation works without a pre-existing private skill/archive.
        self.assertTrue(self.skill.exists())
        self.assertFalse(guidance.archive_path(self.skill).exists())

    def test_core_stays_small(self):
        core = apply_engine.load_skill()
        self.assertLess(len(core), 8000)
        self.assertNotIn('## Learned from runs', core)

    def test_resume_refreshes_review_standard_and_education_without_loading_archive(self):
        self.skill.write_text('HISTORICAL_NOTES ' * 20000)
        with patch.object(apply_engine, 'SKILL_PATH', self.skill), \
             patch.object(profile, 'load_profile', return_value={'education': [{'school':'School A', 'end':'2028-03', 'expected':True}]}):
            for after in ('needs_login', 'captcha', 'needs_answer', 'needs_cover_letter'):
                prompt = apply_engine.resume_message([{'question':'Optional project example?', 'answer':'Please skip this question.'}], self.root, after=after)
                self.assertEqual(prompt.count(guidance.review_instructions()), 1)
                self.assertIn('2028-03', prompt)
                self.assertIn('Please skip this question.', prompt)
                self.assertIn(str(guidance.GUIDES / 'workday.md'), prompt)
                self.assertNotIn('HISTORICAL_NOTES', prompt)
                self.assertLess(len(prompt), 6000)
