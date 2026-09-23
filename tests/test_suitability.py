import tempfile
import unittest
from pathlib import Path

from jobFilter.models import Job
from jobFilter.store import Store
from jobFilter.suitability import assess


class SuitabilityTests(unittest.TestCase):
    def row(self, **screen):
        return {'job': {}, 'screening': {'status': 'ok', 'new_grad_fit': True,
                'requires_citizenship': False, 'verdict': 'likely', **screen}}

    def test_uncertainty_is_not_a_rejection(self):
        for row in ({'job': {}}, self.row(verdict='unlikely'), self.row(new_grad_fit=None),
                    self.row(status='failed'), {'job': {}, 'filter_reason': "country ? not in ['US']"},
                    {'job': {}, 'filter_reason': 'experience not stated in posting'}):
            with self.subTest(row=row):
                self.assertEqual(assess(row)['state'], 'needs_review')

    def test_clear_blockers_and_positive_evidence(self):
        self.assertEqual(assess(self.row())['state'], 'suitable')
        for row in (self.row(new_grad_fit=False), self.row(statement='no_sponsorship'),
                    self.row(requires_citizenship=True),
                    {**self.row(), 'filter_reason': "country ['IN'] not in ['US']"},
                    {**self.row(), 'job': {'min_yoe': 3}},
                    {'job': {'min_yoe': 3}}):
            with self.subTest(row=row):
                review = assess(row)
                self.assertEqual(review['state'], 'not_suitable')
                self.assertTrue(review['reasons'])

    def test_overrides_and_reset_are_state_transitions(self):
        row = self.row(statement='no_sponsorship')
        self.assertEqual(assess(row)['state'], 'not_suitable')
        row['review_overrides'] = {'conclusion': 'needs_review'}
        self.assertEqual(assess(row)['state'], 'needs_review')
        self.assertEqual(assess(row)['automatic_state'], 'not_suitable')
        row['review_overrides'] = {'tags': {'sponsorship': 'supported'}}
        self.assertEqual(assess(row)['state'], 'suitable')
        row['review_overrides']['conclusion'] = 'not_suitable'
        self.assertEqual(assess(row)['state'], 'not_suitable')
        row['review_overrides'] = {'conclusion': None, 'tags': {}}
        self.assertEqual(assess(row)['state'], 'not_suitable')

    def test_manual_edits_survive_screening_rescan_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'jobs.db'
            store = Store(path)
            job = Job.from_hit({'objectID': 'fixture'})
            try:
                store.upsert_many([job], 'first')
                store.save_job_review(job.id, {'conclusion': 'suitable', 'tags': {'new_grad': 'yes'},
                                              'custom_tags': [' priority ', 'priority']})
                store.save_screening(job.id, {'status': 'ok', 'statement': 'no_sponsorship', 'new_grad_fit': False})
                store.upsert_many([job], 'second')
                review = store.query()[0]['review']
                self.assertEqual(review['state'], 'suitable')
                self.assertEqual(review['automatic_state'], 'not_suitable')
                self.assertEqual(review['custom_tags'], ['priority'])
            finally:
                store.close()
            store = Store(path)
            try:
                self.assertEqual(store.get(job.id)['review']['override'], 'suitable')
                reset = store.save_job_review(job.id, {'conclusion': None, 'tags': {}})
                self.assertEqual(reset['review']['state'], 'not_suitable')
                self.assertEqual(reset['review']['custom_tags'], ['priority'])
                self.assertEqual(store.list_applications(), [])
            finally:
                store.close()

    def test_invalid_edits_leave_saved_review_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'jobs.db')
            try:
                job = Job.from_hit({'objectID': 'fixture'})
                store.upsert_many([job], 'first')
                before = store.save_job_review(job.id, {'conclusion': 'not_suitable'})['review']
                for changes in ({'conclusion': 'submitted'}, {'tags': {'new_grad': 'maybe'}},
                                {'tags': {'invented': 'yes'}}, {'custom_tags': ['x' * 51]},
                                {'custom_tags': 'text'}, {'extra': True}):
                    with self.assertRaises(ValueError):
                        store.save_job_review(job.id, changes)
                    self.assertEqual(store.get(job.id)['review'], before)
                with self.assertRaisesRegex(ValueError, 'Unknown job'):
                    store.save_job_review('missing', {})
            finally:
                store.close()
