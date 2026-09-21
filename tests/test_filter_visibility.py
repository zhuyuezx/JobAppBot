import tempfile
import unittest
from pathlib import Path

from jobFilter.filters import apply_rules
from jobFilter.sources import _job
from jobFilter.store import Store


class FilterVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'jobs.db')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def job(self, id, **kw):
        return _job('hiringcafe', id=id, dedup_key='shared-cluster', title='Junior Software Engineer',
                    company='Fixture', apply_url=f'https://example.com/jobs/{id}', **kw)

    def test_distinct_requisitions_in_one_cluster_remain_visible(self):
        jobs = [self.job(str(i), security_clearance='Top Secret/SCI') for i in range(3)]
        kept, excluded = apply_rules(jobs, {'exclude_security_clearance':True})
        self.assertEqual(len(excluded), 3)
        self.assertTrue(all('clearance' in reason for _, reason in excluded))
        self.store.upsert_many(jobs, 'scan', {j.id:r for j,r in excluded})
        self.assertEqual(self.store.count(), 3)
        self.assertEqual(self.store.unscreened(), [])
        self.assertTrue(all(r['filter_reason'] for r in self.store.query()))
        self.store.upsert_many(jobs, 'repeat', {j.id:r for j,r in excluded})
        self.assertEqual(self.store.count(), 3)

    def test_recheck_can_admit_an_excluded_job_without_changing_first_seen(self):
        job=self.job('1')
        self.store.upsert_many([job], 'first', {'1':'category mismatch'})
        original=self.store.get('1')['first_seen']
        self.store.upsert_many([job], 'second')
        self.assertIsNone(self.store.get('1')['filter_reason'])
        self.assertEqual(self.store.get('1')['first_seen'], original)
        self.assertEqual([r['id'] for r in self.store.unscreened()], ['1'])

    def test_identical_url_still_deduplicates_and_a_match_wins(self):
        match=self.job('1')
        other=self.job('2')
        other.apply_url=match.apply_url
        self.store.upsert_many([match,other], 'scan', {'2':'category mismatch'})
        self.assertEqual(self.store.count(),1)
        self.assertIsNone(self.store.get('1')['filter_reason'])
