import datetime as dt
import importlib.util
import unittest
from pathlib import Path


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


summary_module = load('ops_summary', 'meteor-ops-summary.py')
alerts_module = load('ops_alerts', 'meteor-ops-local-alerts.py')
NOW = dt.datetime(2026, 11, 5, 17, 0, tzinfo=dt.timezone.utc)
RESERVE = 32 * 1024**3


def phone(storage):
    return {'state': 'READ_OK', 'devices': {'surya': {
        'http_api': {'status': 'ONLINE'}, 'control_013': {'status': 'ONLINE'},
        'recording': {}, 'storage': storage}}}


def sample(headroom, stamp='2026-11-05T17:00:00+00:00'):
    return {'reserve_ok': True, 'free_bytes': RESERVE + headroom,
            'protected_reserve_bytes': RESERVE, 'last_sample_utc': stamp}


class StorageWarningTests(unittest.TestCase):
    def issues(self, storage):
        return summary_module.issue_classifications({}, phone(storage), {}, {}, {}, NOW)

    def test_low_headroom_warns_before_reserve(self):
        matches = [x for x in self.issues(sample(1024**3)) if x['code'] == 'STORAGE_LOW']
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['reason'], 'RESERVE_HEADROOM_LOW')
        self.assertEqual(matches[0]['target'], 'surya')

    def test_healthy_and_stale_samples_do_not_create_early_warning(self):
        self.assertFalse(any(x['code'] == 'STORAGE_LOW' for x in self.issues(sample(3 * 1024**3))))
        self.assertFalse(any(x['code'] == 'STORAGE_LOW' for x in self.issues(
            sample(1024**3, '2026-11-05T16:00:00+00:00'))))
        self.assertFalse(any(x['code'] == 'STORAGE_LOW' for x in self.issues(
            sample(1024**3, '2026-11-05T17:00:00'))))

    def test_unknown_data_does_not_claim_recovery(self):
        assessment = {'level': 'OK', 'codes': [], 'classifications': [], 'night_fully_accepted': False}
        previous = {'active': ['STORAGE_LOW:surya'], 'last_digest_day': '2026-11-05'}
        doc = {'schema': 'meteormesh.ops-summary.v1', 'assessment': assessment,
               'phone_heartbeat': phone(sample(3 * 1024**3, '2026-11-05T16:00:00+00:00'))}
        event, updated = alerts_module.change(doc, previous, NOW)
        self.assertIn('STORAGE_LOW:surya', updated['active'])
        self.assertFalse(any(e['code'] == 'STORAGE_LOW:surya' for e in event['recoveries']))
        doc['phone_heartbeat'] = phone(sample(int(2.5 * 1024**3)))
        event, updated = alerts_module.change(doc, previous, NOW)
        self.assertIn('STORAGE_LOW:surya', updated['active'])
        self.assertFalse(any(e['code'] == 'STORAGE_LOW:surya' for e in event['recoveries']))
        doc['phone_heartbeat'] = phone(sample(4 * 1024**3))
        event, updated = alerts_module.change(doc, previous, NOW)
        self.assertNotIn('STORAGE_LOW:surya', updated['active'])
        self.assertTrue(any(e['code'] == 'STORAGE_LOW:surya' for e in event['recoveries']))


if __name__ == '__main__':
    unittest.main()
