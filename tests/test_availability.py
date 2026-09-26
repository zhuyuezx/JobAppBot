import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobFilter import apply_engine, profile


class AvailabilityTests(unittest.TestCase):
    def test_new_and_resumed_applications_use_current_availability_not_graduation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profile.json'
            applicant = {'education': [{'end': '2027-06', 'expected': True}],
                         'preferences': {'earliest_start_date': '2027-06-01'}}
            path.write_text(json.dumps(applicant))
            with patch.object(profile, 'PROFILE_PATH', path), \
                 patch.object(profile, 'load_answers', return_value=[]), \
                 patch.object(profile, 'application_resume', return_value={'path': '', 'version': 'sde', 'reason': 'test', 'text': 'Graduation: June 2027'}), \
                 patch.object(apply_engine, 'load_skill', return_value=''):
                app = {'job': {'title': 'Engineer'}}
                initial = apply_engine.build_prompt(app, Path(tmp))
                self.assertIn('use 2027-06-01', initial)
                applicant['preferences'] = {'earliest_start_date': '2027-03-22',
                                            'availability_note': 'I can complete my degree in winter quarter.'}
                path.write_text(json.dumps(applicant))
                for prompt in [apply_engine.build_prompt(app, Path(tmp)),
                               apply_engine.resume_message([], Path(tmp), after='needs_login')]:
                    self.assertIn('use 2027-03-22', prompt)
                    self.assertIn('winter quarter', prompt)
                    self.assertNotIn('use 2027-06-01', prompt)
                    self.assertIn('For graduation questions, use the education record', prompt)
                self.assertEqual(profile.load_profile()['education'], applicant['education'])

    def test_shared_instructions_use_the_supplied_codex_profile(self):
        with patch.object(profile, 'load_profile', side_effect=AssertionError('must use supplied task profile')):
            instructions = profile.availability_instructions({'preferences': {'earliest_start_date': '2027-03-22'}})
        self.assertIn('2027-03-22', instructions)
        self.assertEqual(profile.availability_instructions({}), '')
