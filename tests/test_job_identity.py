import tempfile
import unittest
from pathlib import Path

from jobFilter import duplicates
from jobFilter.job_identity import normalize_apply_url
from jobFilter.models import Job
from jobFilter.store import Store


PAIRS = [
    ('https://careers-ipipeline.icims.com/jobs/1718/software-developer/job?in_iframe=1',
     'https://talent-ipipeline.icims.com/jobs/1718/software-development-engineer/job'),
    ('https://medtronic.wd1.myworkdayjobs.com/redeploymentmedtroniccareers/job/Mounds-View/Software-Test_R72211',
     'https://medtronic.wd1.myworkdayjobs.com/en-US/medtroniccareers/job/Mounds-View/Software-Test_R72211-1/apply'),
    ('https://www.amazon.jobs/en/jobs/10567489/software-development-engineer',
     'https://www.amazon.jobs/jobs/10567489/apply'),
    ('https://jobs.ashbyhq.com/mintmcp/83584770-dd7c-499d-973f-36829bed51ad/application?embed=true',
     'https://jobs.ashbyhq.com/mintmcp/83584770-dd7c-499d-973f-36829bed51ad'),
    ('https://boards.greenhouse.io/spacex/jobs/8861900002?gh_jid=8861900002',
     'https://job-boards.greenhouse.io/spacex/jobs/8861900002'),
    ('https://boards.greenhouse.io/example/jobs/123',
     'https://job-boards.greenhouse.io/embed/job_app?for=example&token=123'),
    ('https://www.example.com/careers/job.456?gh_jid=456',
     'https://example.com/jobs/application?gh_jid=456&utm_source=board'),
    ('https://careers.oracle.com/jobs/#en/sites/jobsearch/job/346357',
     'https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch/requisitions/job/346357'),
]


def job(ident, url, via='hiringcafe'):
    j = Job.from_hit({'objectID': ident})
    j.title, j.company, j.location, j.apply_url, j.via = 'Software Engineer I', 'Example', 'Seattle, WA', url, via
    return j


class IdentityTests(unittest.TestCase):
    def test_workday_year_prefixed_requisitions_are_not_publication_suffixes(self):
        base = 'https://usbank.wd1.myworkdayjobs.com/us_bank_careers/job/Atlanta-GA/Engineer_'
        urls = [base + req for req in ('2026-0028472', '2026-0022352', '2026-0029593', '2026-0027055', '2026-0030686')]
        self.assertEqual(len({normalize_apply_url(u) for u in urls}), 5)
        for url in (urls[0], base + 'R-292618', base + '01866752', base + 'R72211'):
            self.assertEqual(normalize_apply_url(url), normalize_apply_url(url + '-1/apply'))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'jobs.db'
            s = Store(path)
            try:
                for i, url in enumerate(urls):
                    s.upsert_many([job(str(i), url)], 'test')
                self.assertEqual(s.count(), 5)
                s.mark_submitted('0')
                s.conn.execute("UPDATE jobs SET norm_url='workday/usbank.wd1.myworkdayjobs.com/2026'")
                s.conn.execute('PRAGMA user_version=4'); s.conn.commit()
            finally:
                s.close()
            s = Store(path)
            try:
                self.assertEqual(len({r['norm_url'] for r in s.query()}), 5)
                self.assertEqual(s.get_application('0')['status'], 'submitted')
                self.assertTrue(all(d['match_type'] == 'possible' and not d['groupable']
                                    for ds in duplicates.find(s.query(), s.duplicate_candidates()).values() for d in ds))
            finally:
                s.close()

    def test_cross_source_matching_is_shared_and_company_independent(self):
        for company in ('Rubrik', 'Another Employer'):
            with self.subTest(company=company):
                old = {'id': 'employer', 'company': company.lower(),
                       'title': 'Software Engineer - Product Platform Security', 'location': 'Palo Alto, CA',
                       'apply_url': 'https://www.example.com/company/careers/job.8243737?gh_jid=8243737'}
                copy = {**old, 'id': 'aggregator', 'company': company,
                        'location': 'Palo Alto, California, U.S.', 'apply_url': 'https://startup.jobs/engineer-10322885'}
                self.assertEqual(duplicates.classify(old, copy), {'match_type': 'possible', 'groupable': True})
                self.assertTrue(duplicates.find([copy], [old])['aggregator'][0]['groupable'])
                different = {**old, 'id': 'other', 'apply_url': old['apply_url'].replace('8243737', '8243738')}
                self.assertFalse(duplicates.classify(old, different)['groupable'])

    def test_broad_location_only_warns_and_conflicting_countries_do_not_match(self):
        old = {'company': 'Example', 'title': 'Engineer', 'location': 'Seattle, WA'}
        self.assertFalse(duplicates.classify(old, {**old, 'location': 'U.S.'})['groupable'])
        self.assertFalse(duplicates.classify(old, {**old, 'location': None})['groupable'])
        self.assertIsNone(duplicates.classify({**old, 'location': 'Remote, United States'}, {**old, 'location': 'Remote, India'}))

    def test_possible_matches_keep_both_source_records_and_application_histories(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d) / 'jobs.db')
            try:
                old = job('old', 'https://example.com/careers?gh_jid=123', 'speedyapply')
                copy = job('copy', 'https://startup.jobs/engineer-456', 'startupjobs')
                s.upsert_many([old], 'first'); s.mark_submitted('old')
                self.assertEqual([j.id for j in s.upsert_many([copy], 'second')], ['copy'])
                s.mark_unavailable('copy')
                self.assertEqual(s.count(), 2)
                self.assertEqual(s.get_application('old')['status'], 'submitted')
                self.assertEqual(s.get_application('copy')['status'], 'unavailable')
                matched = duplicates.find(s.query(), s.duplicate_candidates())['copy'][0]
                self.assertEqual(matched['app_status'], 'submitted')
                self.assertTrue(matched['groupable'])
                # Later filter changes must not erase evidence of a previous application.
                s.upsert_many([old], 'rescan', {'old': 'outside current search'})
                matched = duplicates.find([s.get('copy')], s.duplicate_candidates())['copy'][0]
                self.assertEqual((matched['id'], matched['app_status']), ('old', 'submitted'))
            finally:
                s.close()

    def test_affirm_renamed_early_career_listings_show_prior_submissions(self):
        rows = []
        for city, old_location, new_location, employer_id, listing_id in [
            ('NYC', 'New York City, NY', 'New York, U.S.', '8008649003', '10318495'),
            ('SF', 'San Francisco, CA', 'San Francisco, California, U.S.', '8010617003', '10318496'),
        ]:
            rows.extend([
                {'id': 'old-' + city, 'via': 'applyguy', 'company': 'Affirm',
                 'title': f'Software Engineer I (New Grad 2027) ({city})', 'location': old_location,
                 'apply_url': f'https://job-boards.greenhouse.io/affirm/jobs/{employer_id}',
                 'first_seen': '2026-10-06', 'app_status': 'submitted'},
                {'id': 'new-' + city, 'via': 'startupjobs', 'company': 'Affirm',
                 'title': f'Software Engineer, Early Career ({city})', 'location': new_location,
                 'apply_url': f'https://startup.jobs/software-engineer-early-career-{city.lower()}-affirm-2-{listing_id}',
                 'first_seen': '2026-10-07'},
            ])
        found = duplicates.find(rows, rows)
        for city in ('NYC', 'SF'):
            self.assertEqual([(r['id'], r['match_type'], r['app_status']) for r in found['new-' + city]],
                             [('old-' + city, 'possible', 'submitted')])

    def test_early_career_alias_keeps_distinct_roles_and_cohorts_separate(self):
        old = {'id': 'old', 'company': 'Affirm', 'title': 'Software Engineer I (New Grad 2027)',
               'location': 'New York City, NY', 'app_status': 'submitted'}
        for changes in [
            {'title': 'Software Engineer II, Early Career'},
            {'title': 'Senior Software Engineer, Early Career'},
            {'title': 'Software Engineer Intern, Early Career'},
            {'title': 'Software Engineer, Backend, Early Career'},
            {'title': 'Software Engineer, Early Career 2028'},
            {'title': 'Software Engineer I (New Grad 2026)'},
            {'title': 'Software Engineer'},
            {'location': 'San Francisco, CA'},
            {'company': 'Different employer'},
        ]:
            with self.subTest(changes=changes):
                other = {**old, 'id': 'other', 'title': 'Software Engineer, Early Career', **changes}
                self.assertEqual(duplicates.find([old, other], [old, other]), {})

    def test_exact_posting_identity_takes_precedence_over_changed_cohort(self):
        old = {'id': 'old', 'title': 'Software Engineer I (New Grad 2026)',
               'apply_url': 'https://job-boards.greenhouse.io/affirm/jobs/8008649003', 'app_status': 'submitted'}
        new = {**old, 'id': 'new', 'title': 'Software Engineer, Early Career 2027'}
        self.assertEqual(duplicates.find([new], [old])['new'][0]['match_type'], 'exact')

    def test_url_variants_share_identity_but_other_requisitions_do_not(self):
        for a,b in PAIRS:
            with self.subTest(a=a):
                self.assertEqual(normalize_apply_url(a), normalize_apply_url(b))
        self.assertNotEqual(normalize_apply_url(PAIRS[-1][0]), normalize_apply_url(PAIRS[-1][0].replace('346357','346358')))
        self.assertNotEqual(normalize_apply_url(PAIRS[0][0]), normalize_apply_url(PAIRS[0][0].replace('ipipeline','differentcompany')))
        self.assertNotEqual(normalize_apply_url(PAIRS[1][0]), normalize_apply_url(PAIRS[1][0].replace('R72211','R72212')))
        self.assertNotEqual(normalize_apply_url('https://example.com/jobs?id=1'), normalize_apply_url('https://example.com/jobs?id=2'))
        self.assertNotEqual(normalize_apply_url('https://example.com/jobs?gh_jid=123'), normalize_apply_url('https://other.com/jobs?gh_jid=123'))
        self.assertNotEqual(normalize_apply_url('https://boards.greenhouse.io/embed/job_app?for=a&token=123'), normalize_apply_url('https://boards.greenhouse.io/embed/job_app?for=b&token=123'))

    def test_same_source_matches_even_with_different_title_location(self):
        for a,b in PAIRS:
            with self.subTest(a=a):
                rows = [{'id':'old','via':'hiringcafe','company':'Example','title':'Engineer','location':'Seattle, WA','apply_url':a,'app_status':'submitted'},
                        {'id':'new','via':'hiringcafe','company':'Example','title':'Software Engineer','location':'US','apply_url':b}]
                found=duplicates.find(rows,rows)
                self.assertEqual(found['new'][0]['match_type'],'exact')
                self.assertEqual(found['new'][0]['app_status'],'submitted')

    def test_possible_reposts_not_confirmed_duplicates_and_conflicting_locations_stay_separate(self):
        rows = [{'id':'a','via':'speedyapply','company':'NCR','title':'Site Reliability Engineer','location':'Atlanta, GA +2','apply_url':'https://ncr.wd1.myworkdayjobs.com/ext/job/Atlanta/Engineer_R0158645'},
                {'id':'b','via':'speedyapply','company':'NCR','title':'Site Reliability Engineer','location':'Atlanta, Georgia','apply_url':'https://ncr.wd1.myworkdayjobs.com/ext/job/Atlanta/Engineer_R0158644'},
                {'id':'other-city','via':'simplify','company':'NCR','title':'Site Reliability Engineer','location':'Austin, TX'}]
        found=duplicates.find(rows,rows)
        self.assertEqual([(r['id'],r['match_type']) for r in found['a']], [('b','possible')])
        self.assertNotIn('other-city',found)
        self.assertFalse(duplicates._compatible_location('Denver, CO +1','California, U.S.'))

    def test_ingestion_keeps_distinct_ids_and_original_history(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/'jobs.db')
            try:
                old=job('old', PAIRS[1][0]);s.upsert_many([old],'first')
                s.mark_submitted('old');s.save_job_review('old',{'custom_tags':['keep'], 'conclusion':'not_suitable'})
                self.assertEqual(s.upsert_many([job('same',PAIRS[1][1])],'second'), [])
                other=job('other', PAIRS[1][0].replace('R72211','R72212'),'simplify')
                self.assertEqual([j.id for j in s.upsert_many([other],'third')],['other'])
                self.assertEqual(s.get_application('old')['status'],'submitted')
                self.assertEqual(s.get('old')['review']['custom_tags'],['keep'])
                self.assertEqual(s.get('old')['review']['state'],'not_suitable')
            finally:s.close()

    def test_exact_url_wins_over_earlier_title_only_match(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d)/'jobs.db')
            try:
                weak = job('weak', 'https://startup.jobs/engineer-123', 'startupjobs')
                exact = job('exact', PAIRS[0][0]); exact.title = 'Different title wording'
                s.upsert_many([weak, exact], 'first')
                before = s.get('weak')['seen_count']
                self.assertEqual(s.upsert_many([job('new', PAIRS[0][1], 'speedyapply')], 'second'), [])
                self.assertEqual(s.get('weak')['seen_count'], before)
                self.assertEqual(s.get('exact')['seen_count'], 2)
            finally:
                s.close()

    def test_migration_keeps_existing_duplicate_rows_and_both_applications(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'jobs.db';s=Store(path)
            # Simulate historical records stored under the old URL formula.
            s.upsert_many([job('a',PAIRS[0][0])],'first')
            s.conn.execute("UPDATE jobs SET norm_url='old-a'");s.conn.commit()
            s.upsert_many([job('b',PAIRS[0][1])],'second')
            s.mark_submitted('a');s.mark_unavailable('b')
            s.conn.execute("UPDATE jobs SET norm_url='old-' || id")
            s.conn.execute('PRAGMA user_version=3');s.conn.commit();s.close()
            s=Store(path)
            try:
                self.assertEqual(s.count(),2)
                self.assertEqual(s.get('a')['norm_url'],s.get('b')['norm_url'])
                self.assertEqual(s.get_application('a')['status'],'submitted')
                self.assertEqual(s.get_application('b')['status'],'unavailable')
                # Exact source ID must update itself, even when an older row shares its URL.
                s.upsert_many([job('b',PAIRS[0][1])],'rescan',{'b':'excluded'})
                self.assertIsNone(s.get('a')['filter_reason'])
                self.assertEqual(s.get('b')['filter_reason'],'excluded')
            finally:s.close()
