"""In-memory only tests: do not access MeteorMesh production HDD."""
import sqlite3
import time
import unittest
from recover_policy import recover

class Scope:
    def allows_queue(self,dev,seg,sha):
        return dev=='cam-ruby' and seg.startswith('ruby_')

def db_open():
    db=sqlite3.connect(':memory:')
    db.execute("""CREATE TABLE jobs (
      id INTEGER PRIMARY KEY, device_id TEXT, segment_id TEXT,
      video_sha256 TEXT, state TEXT, attempts INTEGER,
      claimed_at REAL, finished_utc REAL, last_error TEXT)""")
    return db

def add(db,jid,state,attempts,dev='cam-ruby',claimed=None):
    db.execute("""INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)""",
         (jid,dev,'ruby_20261031T190000Z' if dev=='cam-ruby' else 'viva_20261031T190000Z',
          'a'*64,state,attempts,claimed,None,None))
    db.commit()

class RecoveryTests(unittest.TestCase):
    def test_failed_retried_once_then_terminal(self):
        db=db_open();add(db,1,'FAILED',1)
        self.assertEqual(recover(db,scope=Scope())['failed_requeued'],1)
        self.assertEqual(db.execute('SELECT state,attempts FROM jobs').fetchone(),('WAITING',1))
        db.execute('UPDATE jobs SET state="FAILED",attempts=2');db.commit()
        self.assertEqual(recover(db,scope=Scope())['failed_requeued'],0)
        self.assertEqual(db.execute('SELECT state FROM jobs').fetchone()[0],'FAILED')
    def test_worker_crash_resumed_once(self):
        db=db_open();add(db,1,'RUNNING',1,claimed=time.time()-86400)
        self.assertEqual(recover(db,scope=Scope())['stale_requeued'],1)
        self.assertEqual(db.execute('SELECT state FROM jobs').fetchone()[0],'WAITING')
    def test_stale_second_interruption_is_terminal(self):
        db=db_open();add(db,1,'RUNNING',2,claimed=time.time()-86400)
        self.assertEqual(recover(db,scope=Scope())['stale_exhausted'],1)
        self.assertEqual(db.execute('SELECT state FROM jobs').fetchone()[0],'FAILED')
    def test_active_job_and_offline_other_camera_untouched(self):
        db=db_open();add(db,1,'RUNNING',1,claimed=time.time())
        add(db,2,'FAILED',1,dev='cam-viva')
        self.assertEqual(sum(recover(db,scope=Scope()).values()),0)
        self.assertEqual([x[0] for x in db.execute('SELECT state FROM jobs ORDER BY id')],['RUNNING','FAILED'])
    def test_legacy_waiting_attempt_limit(self):
        db=db_open();add(db,1,'WAITING',2)
        self.assertEqual(recover(db,scope=Scope())['waiting_exhausted'],1)
        self.assertEqual(db.execute('SELECT state FROM jobs').fetchone()[0],'FAILED')
    def test_scope_failure_rolls_back(self):
        db=db_open();add(db,1,'FAILED',1)
        class Bad:
            def allows_queue(self,*args):raise ValueError('bad scope')
        with self.assertRaises(ValueError):recover(db,scope=Bad())
        self.assertEqual(db.execute('SELECT state FROM jobs').fetchone()[0],'FAILED')

if __name__=='__main__':unittest.main()
