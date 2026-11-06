import datetime as dt
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('watch', Path(__file__).with_name('meteor-heartbeat-watch.py'))
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)
NOW = dt.datetime(2026, 11, 5, 17, 0, tzinfo=dt.timezone.utc)


def fixture(version='0.13.6-pin0000-timed-muxer-beta1'):
    control = {'ok': True, 'devices': {'surya': {
        'online': True, 'age_seconds': 20, 'lan_listening': False,
        'app_version': version, 'command_pending': False}}}
    phones = {'surya': {'ok': False}}
    updated = {'devices': {'surya': {'channels': {'http_api': {
        'status': 'UNREACHABLE', 'consecutive_failures': 2,
        'outage_duration_seconds': 300}}, 'safe_recovery': {}}}}
    return control, phones, updated


class Listener0136Tests(unittest.TestCase):
    def test_reviewed_0136_and_legacy_versions(self):
        for version in ('0.13.6-pin0000-timed-muxer-beta1', watch.SAFE_RECOVERY_APP_VERSION):
            c, p, u = fixture(version)
            self.assertEqual(watch.listener_recovery_candidates(c, p, u, NOW), ['surya'])

    def test_unreviewed_version_and_unsafe_conditions_rejected(self):
        c, p, u = fixture('0.13.6-unreviewed')
        self.assertEqual(watch.listener_recovery_candidates(c, p, u, NOW), [])
        for key, value in (('online', False), ('age_seconds', 999), ('command_pending', True),
                           ('lan_listening', True)):
            c, p, u = fixture()
            c['devices']['surya'][key] = value
            self.assertEqual(watch.listener_recovery_candidates(c, p, u, NOW), [])
        c, p, u = fixture()
        p['surya']['ok'] = True
        self.assertEqual(watch.listener_recovery_candidates(c, p, u, NOW), [])

    def test_dry_run_never_sends_and_cooldown_remains(self):
        c, p, u = fixture()
        def sender(_):
            raise AssertionError('dry-run must not send')
        result = watch.apply_safe_listener_recovery(c, p, u, NOW, commit=False, sender=sender)
        self.assertEqual(result['actions'][0]['status'], 'DRY_RUN_ELIGIBLE')
        u['devices']['surya']['safe_recovery'] = {'attempts_utc': [NOW.isoformat()]}
        result = watch.apply_safe_listener_recovery(c, p, u, NOW, commit=True, sender=sender)
        self.assertEqual(result['actions'][0]['status'], 'BACKOFF_COOLDOWN')

    def test_main_does_not_erase_committed_attempt(self):
        c, p, updated = fixture()
        report = {}
        persisted = []
        def fake_apply(_control, _phones, state, _now, *, commit, save_state):
            self.assertTrue(commit)
            state['devices']['surya']['safe_recovery'] = {
                'attempts_utc': [NOW.isoformat()], 'last_result': 'QUEUED'}
            return {'mode': 'COMMIT', 'allowed_action': 'restart_listener_only',
                    'actions': [{'device': 'surya', 'status': 'QUEUED'}]}
        with patch.object(watch, 'utc_now', return_value=NOW), \
             patch.object(watch, 'probe_control', return_value=c), \
             patch.object(watch, 'probe_http', return_value={'ok': True}), \
             patch.object(watch, 'probe_adb', return_value={'ok': True}), \
             patch.object(watch, 'read_state', return_value={}), \
             patch.object(watch, 'evaluate', return_value=(updated, [], report)) as evaluate, \
             patch.object(watch, 'apply_safe_listener_recovery', side_effect=fake_apply), \
             patch.object(watch, 'persist', side_effect=lambda state: persisted.append(state)), \
             patch('sys.argv', ['watch', '--commit', '--quiet', '--allow-safe-listener-recovery']):
            self.assertEqual(watch.main(), 0)
        self.assertEqual(evaluate.call_count, 1)
        self.assertEqual(persisted[-1]['devices']['surya']['safe_recovery']['last_result'], 'QUEUED')
        self.assertEqual(report['safe_recovery']['actions'][0]['status'], 'QUEUED')
        self.assertTrue(report['phone_commands_sent'])


if __name__ == '__main__':
    unittest.main()
