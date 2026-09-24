import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from jobFilter.applyguy import parse_feed, countries_for, REPOSITORY_URL
from jobFilter.filters import apply_rules
from jobFilter.sources import _job, fetch_all
from jobFilter.store import Store


class ApplyGuyTests(unittest.TestCase):
    NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)

    def record(self, **changes):
        return {'id': 'fixture', 'company': 'Example', 'title': 'Junior Software Engineer',
                'location': 'Seattle, WA', 'posted': '2026-09-23', 'eligibility': 'Entry Level',
                'url': 'https://applyguy.ai/jobs?job=fixture',
                'listingUrl': 'https://jobs.lever.co/example/req-1', **changes}

    def test_direct_link_dates_and_no_invented_experience_or_sponsorship(self):
        jobs = parse_feed({'jobs': [self.record()]}, now=self.NOW)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.id, 'applyguy___fixture')
        self.assertEqual(job.apply_url, self.record()['listingUrl'])
        self.assertEqual(job.hc_url, REPOSITORY_URL)
        self.assertEqual(job.countries, ['US'])
        self.assertEqual(job.via, 'applyguy')
        self.assertIsNone(job.min_yoe)
        self.assertIsNone(job.visa_sponsorship)
        self.assertEqual(job.published_at, '2026-09-23T00:00:00+00:00')

    def test_freshness_and_malformed_rows(self):
        records = [self.record(posted=date, id=date) for date in ('2026-09-23','2026-09-21','2026-09-20','2026-09-24','bad')]
        records += [None, {}, self.record(active=False), self.record(listingUrl='javascript:alert(1)'),
                    self.record(listingUrl='https://applyguy.ai/jobs?job=fixture')]
        jobs = parse_feed({'jobs': records}, now=self.NOW)
        self.assertEqual([j.id for j in jobs], ['applyguy___2026-09-23','applyguy___2026-09-21'])
        with self.assertRaises(ValueError):
            parse_feed({'unexpected': []})

    def test_country_guard_does_not_trust_us_feed_description(self):
        for location, url, expected in [
            ('Hyderabad, IN', 'https://bms.wd5.myworkdayjobs.com/bms/job/Hyderabad---TS---IN/Role_R1', ['IN']),
            ('Neu-Ulm, DE', 'https://example.com/job', ['DE']),
            ('Richmond, Melbourne VIC', 'https://example.com/job', ['AU']),
            ('Ft Wayne, IN', 'https://rtx.wd5.myworkdayjobs.com/external/job/US-IN-FT-WAYNE/Role_R1', ['US']),
            ('Indianapolis, IN', 'https://rtx.wd5.myworkdayjobs.com/external/job/Indianapolis-IN-USA/Role_R1', ['US']),
            ('Westfield, IN', 'https://example.com/job', []),
            ('Remote', 'https://example.com/US-headquarters', []),
            ('Remote, U.S.', 'https://example.com/job', ['US']),
            ('Multiple U.S. locations', 'https://example.com/job', ['US']),
        ]:
            with self.subTest(location=location):
                self.assertEqual(countries_for(location, url), expected)
        jobs = parse_feed({'jobs': [self.record(location='Hyderabad, IN')]}, now=self.NOW)
        self.assertEqual(apply_rules(jobs, {'require_countries':['US']})[0], [])

    def test_source_failure_isolated_and_source_can_be_disabled(self):
        cfg = {'sources': {'hiringcafe': False, 'simplify': False, 'startupjobs': False, 'applyguy': True}}
        with patch('jobFilter.sources.fetch_applyguy', side_effect=ValueError('bad feed')) as fetch:
            jobs, counts, errors = fetch_all(cfg)
            self.assertEqual(jobs, [])
            self.assertIn('applyguy', errors)
            cfg['sources']['applyguy'] = False
            self.assertEqual(fetch_all(cfg), ([], {}, {}))
            fetch.assert_called_once()

    def test_url_variants_deduplicate_without_overwriting_history(self):
        pairs = [
            ('https://sample.wd1.myworkdayjobs.com/en-US/External/job/Seattle/Engineer_R123/apply',
             'https://sample.wd1.myworkdayjobs.com/external/job/Seattle-WA/Junior-Engineer_R123'),
            ('https://boards.greenhouse.io/example/jobs/123?gh_jid=123',
             'https://job-boards.greenhouse.io/example/jobs/123'),
            ('https://jobs.lever.co/example/req-1/apply?utm_source=simplify',
             'https://jobs.lever.co/example/req-1'),
            ('https://jobs.ashbyhq.com/example/req-1/application',
             'https://jobs.ashbyhq.com/example/req-1'),
        ]
        for old_url, new_url in pairs:
            with self.subTest(url=old_url), tempfile.TemporaryDirectory() as tmp:
                store = Store(Path(tmp)/'jobs.db')
                try:
                    original = _job('simplify', id='old', dedup_key='old', company='Different name', title='Old title', apply_url=old_url)
                    store.upsert_many([original], 'first')
                    store.mark_submitted('old')
                    store.save_job_review('old', {'custom_tags':['keep me']})
                    incoming = parse_feed({'jobs':[self.record(listingUrl=new_url)]}, now=self.NOW)
                    self.assertEqual(store.upsert_many(incoming, 'second'), [])
                    self.assertEqual(store.count(), 1)
                    self.assertEqual(store.get_application('old')['status'], 'submitted')
                    self.assertEqual(store.get('old')['review']['custom_tags'], ['keep me'])
                    other = parse_feed({'jobs':[self.record(id='distinct', listingUrl=new_url.replace('123','124').replace('req-1','req-2'))]}, now=self.NOW)
                    self.assertNotEqual(original.norm_url(), other[0].norm_url())
                finally:
                    store.close()

    def test_existing_keys_migrate_to_new_canonical_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'jobs.db'
            store = Store(path)
            job = _job('simplify', id='old', dedup_key='old', company='Example', title='Engineer',
                       apply_url='https://jobs.lever.co/example/req-1/apply')
            store.upsert_many([job], 'first')
            first_seen = store.get('old')['first_seen']
            store.conn.execute("UPDATE jobs SET norm_url='old-format'")
            store.conn.execute('PRAGMA user_version=2')
            store.conn.commit(); store.close()
            store = Store(path)
            try:
                self.assertEqual(store.get('old')['norm_url'], 'jobs.lever.co/example/req-1')
                self.assertEqual(store.get('old')['first_seen'], first_seen)
            finally:
                store.close()

    def test_supplemental_match_does_not_clear_existing_rule_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'jobs.db')
            try:
                original = _job('hiringcafe', id='old', dedup_key='old', company='Example', title='Engineer',
                                apply_url=self.record()['listingUrl'], min_yoe=3)
                store.upsert_many([original], 'first', {'old':'requires 3 yoe > 1'})
                incoming = parse_feed({'jobs':[self.record()]}, now=self.NOW)
                self.assertEqual(store.upsert_many(incoming, 'second'), [])
                self.assertEqual(store.get('old')['filter_reason'], 'requires 3 yoe > 1')
                self.assertEqual(store.get('old')['job']['min_yoe'], 3)
            finally:
                store.close()
