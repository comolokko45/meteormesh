import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('monitor', Path(__file__).with_name('meteor-monitor-server.py'))
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class OperationsTruthTests(unittest.TestCase):
    def test_active_alert_changes_ok_to_warning(self):
        level, codes = monitor.merge_local_alerts('OK', ['HISTORICAL_FAILED_JOBS_PRESENT'],
            {'state': 'READ_OK', 'active': ['STORAGE_LOW:surya']})
        self.assertEqual(level, 'WARNING')
        self.assertIn('STORAGE_LOW:surya', codes)
        self.assertEqual(monitor.merge_local_alerts('BLOCKED', [],
            {'state': 'READ_OK', 'active': ['STORAGE_LOW:surya']})[0], 'BLOCKED')

    def test_missing_alert_state_does_not_claim_ok(self):
        level, codes = monitor.merge_local_alerts('OK', [], {'state': 'UNAVAILABLE'})
        self.assertEqual(level, 'WARNING')
        self.assertIn('OPS_ALERT_STATE_UNAVAILABLE', codes)

    def test_symlink_policy_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / 'target.json'
            target.write_text(json.dumps({'schema': 'meteormesh.negative-retention-activation.v1',
                                          'auto_delete_enabled': True}))
            alias = root / 'link.json'
            os.symlink(target, alias)
            with patch.object(monitor, 'RETENTION_POLICY', alias):
                self.assertFalse(monitor.read_retention_policy()['auto_delete_enabled'])
                self.assertEqual(monitor.read_retention_policy()['policy_state'], 'UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
