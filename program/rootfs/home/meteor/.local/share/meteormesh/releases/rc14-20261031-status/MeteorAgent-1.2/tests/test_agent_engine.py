import copy
import json
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from agent_engine import EXPECTED_UUID, evaluate, SCHEMA

NOW = datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc)


def good():
    return {
      'schema':SCHEMA,'checked_tr':'2026-10-30T12:58:00+03:00','status':'OK',
      'hdd':{'path':'/srv/meteor-data','is_mount':True,'fstype':'ext4',
             'uuid':EXPECTED_UUID,'free_gib':433.8},
      'database':{'state':'READ_ONLY_OK','ingest':{},'jobs':{},'detector_runs':0},
      'last_morning':{'state':'NO_REPORT_YET','outcome':None},
      'morning_timer':{'enabled':'disabled','active':'inactive'},
      'memory':{'available_gib':2.0},'ntp_synchronized':'yes', 'issues':[],
      'read_only': True, 'runs_detector': False, 'can_delete':False,
    }


class EngineTests(unittest.TestCase):
    def ev(self, status, **kw):
        return evaluate(status, now=NOW, **kw)

    def codes(self, x):
        return [x['code'] for x in self.ev(x)['causes']]

    def test_healthy_report_is_not_completed_night(self):
        r=self.ev(good())
        self.assertEqual(r['level'], 'OBSERVED_OK')
        self.assertIn('Yeni gece tamamlandı iddiası yok', r['summary'])
        self.assertEqual(r['actions_executed'], [])
        self.assertFalse(r['morning_timer_enable_allowed'])

    def test_missing_mount_never_healthy(self):
        x=good();x['hdd']['is_mount']=False
        self.assertEqual(self.codes(x), ['STORAGE_UNVERIFIED'])

    def test_wrong_uuid_blocks(self):
        x=good();x['hdd']['uuid']='different';self.assertIn('STORAGE_UNVERIFIED',self.codes(x))

    def test_missing_uuid_blocks(self):
        x=good();del x['hdd']['uuid'];self.assertIn('STORAGE_UNVERIFIED',self.codes(x))

    def test_wrong_filesystem_blocks(self):
        x=good();x['hdd']['fstype']='xfs';self.assertIn('STORAGE_UNVERIFIED',self.codes(x))

    def test_wrong_mount_path_blocks(self):
        x=good();x['hdd']['path']='/tmp';self.assertIn('STORAGE_UNVERIFIED',self.codes(x))

    def test_unverified_mount_source_blocks(self):
        x=good();x['hdd']['mount_verification']='UNKNOWN';self.assertIn('STORAGE_UNVERIFIED',self.codes(x))

    def test_disk_failure_suppresses_derivative_db_failure(self):
        x=good();x['hdd']['uuid']='bad';x['database']['state']='READ_ERROR'
        x['database']['jobs']={'FAILED':2};x['hdd']['free_gib']=2
        self.assertNotIn('DB_READ_ERROR',self.codes(x))
        self.assertNotIn('FAILED_JOBS',self.codes(x))
        self.assertNotIn('LOW_SPACE',self.codes(x))

    def test_morning_timer_enabled_is_independent_critical(self):
        x=good();x['morning_timer']['enabled']='enabled'
        self.assertEqual(self.codes(x), ['MORNING_ENABLED'])
        self.assertFalse(self.ev(x)['mutation_enabled'])

    def test_missing_timer_state_not_assumed_off(self):
        x=good();x['morning_timer']['enabled']='UNKNOWN'
        self.assertIn('TIMER_STATE_UNKNOWN',self.codes(x))

    def test_morning_timer_active_is_critical(self):
        x=good();x['morning_timer']['active']='active'
        self.assertIn('MORNING_ENABLED',self.codes(x))

    def test_low_space_is_planning_only(self):
        x=good();x['hdd']['free_gib']=100
        self.assertIn('LOW_SPACE',self.codes(x))
        self.assertFalse(self.ev(x)['auto_retry_enabled'])

    def test_failed_jobs_report_not_retried(self):
        x=good();x['database']['jobs']={'FAILED':3}
        self.assertIn('FAILED_JOBS',self.codes(x))
        self.assertEqual(self.ev(x)['actions_executed'],[])

    def test_partial_morning_not_closed(self):
        x=good();x['last_morning']['outcome']='PARTIAL_REVIEW_REQUIRED'
        self.assertIn('PARTIAL_NIGHT',self.codes(x))

    def test_clock_unsynced_not_changed(self):
        x=good();x['ntp_synchronized']='no'
        self.assertIn('CLOCK_UNSYNCED',self.codes(x))

    def test_memory_low_reports(self):
        x=good();x['memory']['available_gib']=0.2
        self.assertIn('MEMORY_LOW',self.codes(x))

    def test_stale_status_unverified(self):
        x=good();x['checked_tr']='2026-10-30T09:00:00+03:00'
        self.assertIn('EVIDENCE_STALE',self.codes(x))
        self.assertEqual(self.ev(x)['level'],'UNVERIFIED')

    def test_missing_timestamp_unverified(self):
        x=good();x['checked_tr']='broken'
        self.assertEqual(self.ev(x)['level'],'UNVERIFIED')

    def test_future_timestamp_unverified(self):
        x=good();x['checked_tr']='2026-10-30T14:00:00+03:00'
        self.assertIn('EVIDENCE_STALE',self.codes(x))

    def test_missing_security_assertion(self):
        x=good();x['can_delete']=True
        self.assertIn('EVIDENCE_INVALID',self.codes(x))
        self.assertEqual(self.ev(x)['level'],'UNVERIFIED')

    def test_missing_schema_invalid(self):
        x=good();x['schema']='untrusted'
        self.assertIn('EVIDENCE_INVALID',self.codes(x))

    def test_non_dict_invalid(self):
        for x in [None, 'hi', [], 123]:
            self.assertEqual(self.ev(x)['level'],'UNVERIFIED')

    def test_unknown_issue_does_not_echo_untrusted_commands(self):
        x=good();x['issues']=[{'code':'sudo rm -rf /','level':'CRITICAL','details':'secret'}]
        r=self.ev(x)
        self.assertIn('STATUS_ISSUES',[c['code'] for c in r['causes']])
        self.assertNotIn('sudo',json.dumps(r))
        self.assertNotIn('secret',json.dumps(r))

    def test_issue_list_too_long_invalid(self):
        x=good();x['issues']=[{'code':'HDD_FREE_LOW'}]*201
        self.assertEqual(self.ev(x)['level'],'UNVERIFIED')

    def test_timestamp_only_does_not_change_fingerprint(self):
        x=good();x['database']['jobs']={'FAILED':1}
        a=self.ev(x)
        x['checked_tr']='2026-10-30T12:59:00+03:00'
        b=self.ev(x)
        self.assertEqual(a['incident_fingerprint'],b['incident_fingerprint'])

    def test_dedup_key_works_without_writing(self):
        x=good();x['database']['jobs']={'FAILED':2}
        first=self.ev(x)
        next_=self.ev(x,previous_fingerprint=first['incident_fingerprint'])
        self.assertTrue(first['alert']['suggest_emit'])
        self.assertFalse(next_['alert']['suggest_emit'])
        self.assertFalse(next_['alert']['delivered'])

    def test_known_issue_no_shell_execution(self):
        x=good();x['issues']=[{'code':'DETECTOR_FAILED_JOBS','level':'WARNING'}]
        self.assertEqual(self.ev(x)['actions_executed'],[])

    def test_input_is_not_mutated(self):
        x=good();old=copy.deepcopy(x)
        self.ev(x)
        self.assertEqual(old,x)

    def test_unknown_status_is_unverified(self):
        x=good();x['status']='SUPER_HEALTHY'
        self.assertEqual(self.ev(x)['level'],'UNVERIFIED')

    def test_upstream_warning_without_details_not_healthy(self):
        x=good();x['status']='WARNING'
        self.assertIn('STATUS_ISSUES',self.codes(x))

    def test_setup_waiting_not_ok(self):
        x=good();x['status']='SETUP_WAITING_FOR_PHONES'
        self.assertEqual(self.ev(x)['level'],'INCOMPLETE')

    def test_missing_free_space_denied(self):
        x=good();x['hdd']['free_gib']=None
        self.assertIn('STORAGE_UNVERIFIED',self.codes(x))

    def test_no_hidden_side_effects(self):
        x=good()
        with patch('subprocess.run', side_effect=AssertionError('shell')) as sh:
            self.ev(x)
            sh.assert_not_called()


if __name__=='__main__':unittest.main()
