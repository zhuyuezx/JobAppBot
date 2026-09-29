import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from jobFilter import sources
from jobFilter.speedyapply import fetch_speedyapply, parse_feed
from jobFilter.store import Store


class SpeedyApplyTests(unittest.TestCase):
    NOW = datetime(2026, 9, 28, 20, tzinfo=timezone.utc)

    def feed(self, age='0d', location='Seattle, WA +2', salary=False, url='https://jobs.lever.co/example/one'):
        return ('<!-- TABLE_OTHER_START -->\n'
                '| Company | Position | Location | Posting | Age |\n'
                '|---|---|---|---|---|\n'
                '| <a href="https://example.com"><strong>Example &amp; Co</strong></a> | '
                'Software Engineer | ' + location + ' | ' + ('$100k/yr | ' if salary else '') +
                f'<a href="{url}"><img alt="Apply"/></a> | {age} |\n<!-- TABLE_END -->')

    def test_both_table_shapes_and_unknown_eligibility(self):
        for salary in [True, False]:
            job, = parse_feed(self.feed(salary=salary), self.NOW, now=self.NOW)
            self.assertEqual(job.company, 'Example & Co')
            self.assertEqual(job.countries, ['US'])
            self.assertEqual(job.via, 'speedyapply')
            self.assertEqual(job.apply_url, 'https://jobs.lever.co/example/one')
            self.assertIsNone(job.min_yoe)
            self.assertIsNone(job.visa_sponsorship)
            self.assertTrue(job.raw['published_at_estimated'])

    def test_age_is_anchored_to_commit_and_stale_feed_ages_out(self):
        text = self.feed(age='2d')
        job, = parse_feed(text, self.NOW, now=self.NOW)
        self.assertEqual(job.published_at, '2026-09-26T20:00:00+00:00')
        self.assertEqual(parse_feed(text, self.NOW, now=datetime(2026, 9, 29, 20, tzinfo=timezone.utc)), [])
        self.assertEqual(parse_feed(self.feed(age='3d'), self.NOW, now=self.NOW), [])

    def test_no_forced_us_classification_and_malformed_rows(self):
        for location, country in [('Hyderabad, IN', ['IN']), ('Remote - USA', ['US']), ('Remote', [])]:
            job, = parse_feed(self.feed(location=location), self.NOW, now=self.NOW)
            self.assertEqual(job.countries, country)
        for text in [self.feed(age='unknown'), self.feed(url='javascript:bad'), self.feed().replace('<a href="https://jobs.lever.co/example/one"><img alt="Apply"/></a>', 'Closed')]:
            self.assertEqual(parse_feed(text, self.NOW, now=self.NOW), [])
        with self.assertRaises(ValueError):
            parse_feed('<html>Error</html>', self.NOW, now=self.NOW)
        self.assertEqual(len(parse_feed(self.feed() + '\n' + self.feed(), self.NOW, now=self.NOW)), 1)

    def test_existing_source_exclusion_and_application_history_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'jobs.db')
            try:
                old = sources._job('simplify', id='old', dedup_key='old', company='Example', title='Engineer',
                                   apply_url='https://jobs.lever.co/example/one/apply', min_yoe=4)
                store.upsert_many([old], 'first', {'old': 'requires 4 yoe > 1'})
                store.mark_submitted('old')
                store.save_job_review('old', {'custom_tags': ['keep']})
                jobs = parse_feed(self.feed(), self.NOW, now=self.NOW)
                self.assertEqual(store.upsert_many(jobs, 'second'), [])
                self.assertEqual(store.get('old')['filter_reason'], 'requires 4 yoe > 1')
                self.assertEqual(store.get('old')['review']['custom_tags'], ['keep'])
                self.assertEqual(store.get_application('old')['status'], 'submitted')
            finally:
                store.close()

    def test_source_failure_isolated_and_disable_works(self):
        cfg = {'sources': {name: False for name in sources.DEFAULT_SOURCES}}
        cfg['sources'].update(speedyapply=True, simplify=True)
        with patch.object(sources, 'fetch_speedyapply', side_effect=ValueError('bad feed')) as fetch, \
             patch.object(sources, 'fetch_simplify', return_value=[]):
            self.assertEqual(sources.fetch_all(cfg)[1], {'simplify': 0})
            self.assertIn('speedyapply', sources.fetch_all(cfg)[2])
            cfg['sources']['speedyapply'] = False
            self.assertEqual(sources.fetch_all(cfg)[2], {})
            self.assertEqual(fetch.call_count, 2)

    def test_fetch_uses_immutable_commit_and_reuses_body_cache(self):
        sha = 'a' * 40
        commits = [{'sha': sha, 'commit': {'committer': {'date': datetime.now(timezone.utc).isoformat()}}}]
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(sources, 'CACHE_DIR', Path(tmp)), \
             patch.object(sources, '_cached_get_json', return_value=commits), \
             patch.object(sources.requests, 'get', return_value=Mock(text=self.feed())) as get:
            first = fetch_speedyapply({})
            self.assertEqual(first[0].id, fetch_speedyapply({})[0].id)
            self.assertEqual(get.call_count, 1)
            self.assertIn('/' + sha + '/NEW_GRAD_USA.md', get.call_args.args[0])
            self.assertEqual(json.loads((Path(tmp) / 'speedyapply-feed.json').read_text())['sha'], sha)
