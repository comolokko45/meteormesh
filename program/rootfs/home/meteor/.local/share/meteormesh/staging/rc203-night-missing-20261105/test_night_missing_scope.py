import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('status', Path(__file__).with_name('MeteorMesh-status-Hub09.py'))
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)


class NightMissingScopeTests(unittest.TestCase):
    def test_current_and_carryover_are_separate(self):
        self.assertEqual(status._current_night_missing({'total': 99, 'by_scope_and_device':
            {'carryover': {'cam-ruby': 48, 'cam-surya': 48, 'cam-viva': 3}}}), 0)
        self.assertEqual(status._current_night_missing({'total': 4, 'by_scope_and_device':
            {'night': {'cam-ruby': 1}, 'carryover': {'cam-viva': 3}}}), 1)

    def test_unknown_and_malformed_are_not_zero(self):
        for value in (None, {'total': 99}, {'total': 2, 'by_scope_and_device': {'night': {'cam-ruby': 1}}},
                      {'total': 1, 'by_scope_and_device': {'night': {'cam-ruby': True}}}):
            self.assertIsNone(status._current_night_missing(value))
        self.assertEqual(status._current_night_missing({'total': 0}), 0)

    def test_latest_report_current_queue_not_subtracted_by_carryover(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'morning-reports').mkdir()
            (root / 'morning-reports' / 'morning_20261105T170000Z_a.json').write_text(json.dumps({
                'night_id': '20261105-night', 'phase': 'sync', 'night_queue': {'WAITING': 42},
                'carryover_queue': {'WAITING': 123, 'FAILED': 119},
                'historical_queue_excluded_from_night_status': True,
                'waiting_missing_source': {'total': 99, 'by_scope_and_device':
                    {'carryover': {'cam-ruby': 48, 'cam-surya': 48, 'cam-viva': 3}}}}))
            report = status.morning_details(root, current_night_id='20261105-night')
            result = status.analysis_scope_details({'jobs': {}}, report)
            self.assertEqual(result['waiting_source_missing'], 0)
            self.assertEqual(result['waiting_actionable_estimate'], 42)
            self.assertEqual(result['historical_failed_review_only'], 119)

    def test_unknown_scope_does_not_claim_actionable_count(self):
        report = {'state': 'READ_OK', 'scope_is_current': True,
                  'historical_queue_excluded_from_night_status': True,
                  'night_queue': {'WAITING': 10}, 'waiting_source_missing': None}
        result = status.analysis_scope_details({'jobs': {}}, report)
        self.assertIsNone(result['waiting_actionable_estimate'])
        self.assertFalse(result['waiting_source_missing_known'])


if __name__ == '__main__':
    unittest.main()
