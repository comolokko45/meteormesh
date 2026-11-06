import datetime as dt
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('watch', Path(__file__).with_name('meteor-heartbeat-watch.py'))
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)
NOW = dt.datetime(2026, 11, 5, 17, 0, tzinfo=dt.timezone.utc)


def state(http, queued_at):
    return {'devices': {'surya': {'channels': {'http_api': {'status': http}},
        'safe_recovery': {'last_result': 'QUEUED', 'last_command_queued_utc': queued_at,
                          'attempts_utc': [queued_at]}}}}


class ListenerVerificationTests(unittest.TestCase):
    def test_later_http_reachable_is_observation_not_causal_claim(self):
        doc = state('ONLINE', (NOW - dt.timedelta(minutes=5)).isoformat())
        got = watch.verify_listener_observations(doc, NOW)
        self.assertEqual(got, [{'device': 'surya', 'status': 'HTTP_REACHABLE_AFTER_REQUEST'}])
        self.assertEqual(doc['devices']['surya']['safe_recovery']['attempts_utc'],
                         [(NOW - dt.timedelta(minutes=5)).isoformat()])
        self.assertEqual(watch.verify_listener_observations(doc, NOW), [])

    def test_unreachable_waits_then_times_out(self):
        doc = state('UNREACHABLE', (NOW - dt.timedelta(minutes=5)).isoformat())
        self.assertEqual(watch.verify_listener_observations(doc, NOW), [])
        got = watch.verify_listener_observations(doc, NOW + dt.timedelta(hours=1))
        self.assertEqual(got[0]['status'], 'UNCONFIRMED_AFTER_TIMEOUT')

    def test_invalid_or_future_timestamp_cannot_claim_success(self):
        for stamp in ('invalid', (NOW + dt.timedelta(minutes=1)).isoformat()):
            doc = state('ONLINE', stamp)
            self.assertEqual(watch.verify_listener_observations(doc, NOW), [])
            self.assertEqual(doc['devices']['surya']['safe_recovery']['last_result'], 'QUEUED')


if __name__ == '__main__':
    unittest.main()
