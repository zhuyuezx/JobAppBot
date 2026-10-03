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
    ('https://careers.oracle.com/jobs/#en/sites/jobsearch/job/346357',
     'https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch/requisitions/job/346357'),
]


def job(ident, url, via='hiringcafe'):
    j = Job.from_hit({'objectID': ident})
    j.title, j.company, j.location, j.apply_url, j.via = 'Software Engineer I', 'Example', 'Seattle, WA', url, via
    return j


class IdentityTests(unittest.TestCase):
    def test_url_variants_share_identity_but_other_requisitions_do_not(self):
        for a,b in PAIRS:
            with self.subTest(a=a):
                self.assertEqual(normalize_apply_url(a), normalize_apply_url(b))
        self.assertNotEqual(normalize_apply_url(PAIRS[-1][0]), normalize_apply_url(PAIRS[-1][0].replace('346357','346358')))
        self.assertNotEqual(normalize_apply_url(PAIRS[0][0]), normalize_apply_url(PAIRS[0][0].replace('ipipeline','differentcompany')))
        self.assertNotEqual(normalize_apply_url(PAIRS[1][0]), normalize_apply_url(PAIRS[1][0].replace('R72211','R72212')))
        self.assertNotEqual(normalize_apply_url('https://example.com/jobs?id=1'), normalize_apply_url('https://example.com/jobs?id=2'))

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
