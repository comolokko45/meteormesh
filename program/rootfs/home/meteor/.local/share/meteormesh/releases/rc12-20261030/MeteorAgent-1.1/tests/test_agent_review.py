import copy
import unittest
from agent_engine import evaluate
from agent_review import make_review
from test_agent_engine import good, NOW

class ReviewTests(unittest.TestCase):
    def check(self, snapshot):
        return make_review(snapshot, evaluate(snapshot, now=NOW))
    def test_no_jobs_does_not_claim_night_complete(self):
        r=self.check(good()); self.assertEqual(r['state'],'OBSERVE');self.assertNotIn('tamamlandı.',r['operator_next_step'])
    def test_queue_is_existing_hub_queue(self):
        x=good();x['database']['jobs']={'PENDING':2,'WAITING':1,'RUNNING':1}
        r=self.check(x); self.assertEqual(r['hub_queue_counts'],{'failed':0,'waiting':3,'running':1})
        self.assertEqual(r['reason'],'HUB_JOBS_PRESENT')
    def test_failures_are_not_retried(self):
        x=good(); x['database']['jobs']={'FAILED':5}; r=self.check(x)
        self.assertEqual(r['reason'],'FAILED_JOBS_PRESENT');self.assertFalse(r['can_auto_retry'])
    def test_disk_missing_overrides_job_failure(self):
        x=good();x['hdd']['is_mount']=False;x['database']['jobs']={'FAILED':3}
        self.assertEqual(self.check(x)['reason'],'STORAGE_NOT_VERIFIED')
    def test_wrong_uuid_blocks(self):
        x=good();x['hdd']['uuid']='wrong'
        self.assertEqual(self.check(x)['state'],'BLOCKED')
    def test_timer_enabled_blocks(self):
        x=good();x['morning_timer']['enabled']='enabled'
        self.assertEqual(self.check(x)['reason'],'MORNING_TIMER_NOT_SAFELY_DISABLED')
    def test_timer_missing_blocks(self):
        x=good();x['morning_timer']['enabled']='UNKNOWN'
        self.assertEqual(self.check(x)['state'],'BLOCKED')
    def test_stale_input_no_queue_action(self):
        x=good();x['checked_tr']='2026-01-01T00:00:00+03:00';r=self.check(x)
        self.assertEqual(r['state'],'UNKNOWN');self.assertFalse(r['can_enqueue'])
    def test_invalid_security_assertion_blocks(self):
        x=good();x['can_delete']=True
        self.assertEqual(self.check(x)['state'],'UNKNOWN')
    def test_db_unreadable(self):
        x=good();x['database']['state']='READ_ERROR'
        self.assertEqual(self.check(x)['reason'],'DATABASE_UNAVAILABLE')
    def test_unknown_job_counts_fail_closed(self):
        x=good();x['database']['jobs']={'FAILED':True,'PENDING':'999999'}
        r=self.check(x);self.assertEqual(r['hub_queue_counts'],{'failed':0,'waiting':0,'running':0})
        self.assertFalse(r['can_auto_retry'])
    def test_never_modifies_input(self):
        x=good(); before=copy.deepcopy(x);self.check(x);self.assertEqual(x,before)
    def test_no_actions_under_any_state(self):
        samples=[good()]
        for field,value in [('status','CRITICAL'),('status','WARNING')]:
            x=good();x[field]=value;samples.append(x)
        for x in samples:
            r=self.check(x)
            self.assertEqual(r['actions_executed'],[])
            self.assertEqual(r['agent_permission'],'OBSERVE_ONLY')
            self.assertEqual(r['mm01_production_gate'],'OPEN_NOT_INTEGRATED')
