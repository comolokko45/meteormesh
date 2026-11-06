import datetime as dt
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent = load('agent', 'agent_engine.py')
wrapper = load('wrapper', 'meteor-status-agent.py')
NOW = dt.datetime(2026, 11, 5, 17, 0, tzinfo=dt.timezone.utc)


def good():
    return {'schema': agent.SCHEMA, 'checked_tr': '2026-11-05T20:00:00+03:00',
            'status': 'OK', 'read_only': True, 'runs_detector': False, 'can_delete': False,
            'hdd': {'path': '/srv/meteor-data', 'is_mount': True, 'fstype': 'ext4',
                    'uuid': agent.EXPECTED_UUID, 'free_gib': 300},
            'database': {'state': 'READ_ONLY_OK', 'jobs': {}},
            'morning_timer': {'enabled': 'enabled', 'active': 'active'},
            'last_morning': {'state': 'NO_REPORT_YET', 'outcome': None},
            'memory': {'available_gib': 2.0}, 'ntp_synchronized': 'yes', 'issues': []}


class AgentAlertsTests(unittest.TestCase):
    def test_open_local_warning_prevents_false_ok(self):
        data = good()
        data['local_alerts'] = {'state': 'READ_OK', 'active': ['STORAGE_LOW:surya']}
        result = agent.evaluate(data, now=NOW)
        self.assertEqual(result['level'], 'WARNING')
        self.assertIn('LOCAL_ALERTS_OPEN', [x['code'] for x in result['causes']])
        self.assertEqual(result['actions_executed'], [])

    def test_unknown_alert_state_is_warning(self):
        data = good()
        data['local_alerts'] = {'state': 'UNAVAILABLE', 'active': []}
        result = agent.evaluate(data, now=NOW)
        self.assertIn('LOCAL_ALERTS_UNAVAILABLE', [x['code'] for x in result['causes']])

    def test_private_alert_state_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'alerts.json'
            p.write_text(json.dumps({'schema': 'meteormesh.ops-alert-state.v1', 'active': ['STORAGE_LOW:surya']}))
            os.chmod(p, 0o600)
            with patch.object(wrapper, 'ALERT_STATE', p):
                self.assertEqual(wrapper.read_local_alerts()['active'], ['STORAGE_LOW:surya'])
                os.chmod(p, 0o644)
                self.assertEqual(wrapper.read_local_alerts()['state'], 'UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
