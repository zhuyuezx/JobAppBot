import unittest
from unittest.mock import Mock, patch

from jobFilter.descriptions import fetch_description


class YCDescriptionTests(unittest.TestCase):
    def setUp(self):
        self.row = {'id': 'hc-id', 'job': {'via': 'hiringcafe',
                    'apply_url': 'https://www.ycombinator.com/careers?job_id=97346'}}

    def test_original_visa_field_precedes_description_and_aggregator(self):
        response = Mock()
        response.json.return_value = {'job': {'id': 97346,
            'pretty_sponsors_visa': 'US citizen/visa only',
            'description': '<p>We are willing to sponsor certain visas.</p>'}}
        with patch('jobFilter.descriptions.requests.get', return_value=response) as get, \
             patch('jobFilter.descriptions.HiringCafeClient') as hc:
            text = fetch_description(self.row)
        self.assertTrue(text.startswith('YC Visa Sponsorship: US citizen/visa only'))
        self.assertIn('willing to sponsor', text)
        self.assertIn('/embed/y-combinator/jobs/97346', get.call_args.args[0])
        hc.assert_not_called()

    def test_failed_original_verification_is_not_silently_hidden(self):
        with patch('jobFilter.descriptions.requests.get', side_effect=RuntimeError('offline')):
            self.assertIn('verification unavailable', fetch_description(self.row))

    def test_other_hiringcafe_jobs_keep_existing_fetcher(self):
        self.row['job']['apply_url'] = 'https://example.com/jobs/123'
        with patch('jobFilter.descriptions.HiringCafeClient') as hc:
            hc.return_value.job_description_text.return_value = 'Original description'
            self.assertEqual(fetch_description(self.row), 'Original description')
