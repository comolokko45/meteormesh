#!/usr/bin/env python3
"""MeteorMesh RC1.5.2: safe, scope-bounded one-retry queue recovery.

No video reads, no SQLite writes, no service starts. Builds a versioned Hub
copy; one user systemd analysis ExecStart override; old release kept intact.
Installer runs in the meteor account after verifying the RC1.5.1 baseline.
"""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

HOME = Path.home()
RELEASES = HOME/'.local/share/meteormesh/releases'
SRC = RELEASES/'rc15-20261031-consolidated/MeteorHub'
ROOT = RELEASES/'rc152-20261101-bounded-retry'
DST = ROOT/'MeteorHub'
DROP = HOME/'.config/systemd/user/meteormesh-analyze.service.d/zzz-rc152-bounded-queue.conf'
SERVICE = 'meteormesh-analyze.service'
MORNING = 'meteormesh-morning.service'

RECOVERY = '''"""Bounded recovery for already-stored MeteorMesh analysis jobs.

Exactly one retry after an ordinary failure; at most two claims total for stale
worker jobs too. Scope validation prevents unrelated old/test footage reruns.
"""
from __future__ import annotations
import time
from hub_workflow import DEFAULT_JOB_LEASE_SECONDS


def recover(db, *, scope):
    if scope is None:
        raise ValueError('APPROVED_NIGHT_SCOPE_REQUIRED')
    threshold = time.time() - DEFAULT_JOB_LEASE_SECONDS
    counts = {'failed_requeued':0, 'stale_requeued':0, 'stale_exhausted':0, 'waiting_exhausted':0}
    db.execute('BEGIN IMMEDIATE')
    try:
        rows = db.execute("""
            SELECT id,device_id,segment_id,video_sha256,state,attempts
            FROM jobs WHERE
                (state='FAILED' AND attempts < 2)
                OR (state='RUNNING' AND claimed_at IS NOT NULL
                    AND claimed_at < ?)
                OR (state='WAITING' AND attempts >= 2)
            ORDER BY id""", (threshold,)).fetchall()
        for jid, dev, seg, sha, state, attempts in rows:
            if not scope.allows_queue(dev, seg, sha):
                continue
            if state == 'FAILED':
                cur = db.execute("""UPDATE jobs SET state='WAITING',
                    claimed_at=NULL, last_error=NULL, finished_utc=NULL
                    WHERE id=? AND state='FAILED' AND attempts<2""", (jid,))
                counts['failed_requeued'] += cur.rowcount
            elif state == 'WAITING':
                cur = db.execute("""UPDATE jobs SET state='FAILED',
                    claimed_at=NULL, finished_utc=?,
                    last_error='Retry limit exhausted (previously requeued)'
                    WHERE id=? AND state='WAITING' AND attempts>=2""",
                    (time.time(), jid))
                counts['waiting_exhausted'] += cur.rowcount
            elif attempts < 2:
                cur = db.execute("""UPDATE jobs SET state='WAITING',
                    claimed_at=NULL, last_error='Recovered expired worker lease'
                    WHERE id=? AND state='RUNNING' AND attempts<2""", (jid,))
                counts['stale_requeued'] += cur.rowcount
            else:
                cur = db.execute("""UPDATE jobs SET state='FAILED',
                    claimed_at=NULL, finished_utc=?,
                    last_error='Retry limit exhausted after worker interruption'
                    WHERE id=? AND state='RUNNING' AND attempts>=2""",
                    (time.time(), jid))
                counts['stale_exhausted'] += cur.rowcount
        db.commit()
        return counts
    except BaseException:
        db.rollback()
        raise
'''

TEST = '''"""In-memory only tests: do not access MeteorMesh production HDD."""
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
''' 

ANCHOR='                report["stale_jobs_reclaimed"] = reclaim_stale(db, scope=scope)'
REPLACEMENT='''                if scope is not None:
                    from recover_policy import recover
                    recovery = recover(db, scope=scope)
                    report["stale_jobs_reclaimed"] = recovery["stale_requeued"]
                    report["failed_jobs_retried"] = recovery["failed_requeued"]
                    report["stale_jobs_exhausted"] = recovery["stale_exhausted"]
                else:
                    report["stale_jobs_reclaimed"] = reclaim_stale(db, scope=scope)'''


def cmd(*args, check=True):
    p = subprocess.run(args,capture_output=True,text=True,timeout=20)
    if check and p.returncode:
        raise RuntimeError('COMMAND_FAILED: '+' '.join(args[:4])+' '+p.stderr.strip()[:150])
    return p.stdout.strip()


def assert_idle():
    for name in (SERVICE,MORNING):
        value=cmd('systemctl','--user','is-active',name,check=False)
        if value not in ('inactive','failed'):
            raise RuntimeError('UNIT_NOT_IDLE: '+name+'='+value)


def assert_baseline():
    if os.geteuid()==0:raise RuntimeError('NOT_AS_ROOT')
    assert_idle()
    if not SRC.is_dir() or SRC.is_symlink() or not (SRC/'meteor_morning.py').is_file():
        raise RuntimeError('RC15_SOURCE_NOT_FOUND')
    if ROOT.exists() or ROOT.is_symlink() or DROP.exists() or DROP.is_symlink():
        raise RuntimeError('RC152_ALREADY_PRESENT')
    unit=cmd('systemctl','--user','show',SERVICE,'-p','ExecStart','--value')
    post=cmd('systemctl','--user','show',SERVICE,'-p','ExecStopPost','--value')
    timeout=cmd('systemctl','--user','show',SERVICE,'-p','TimeoutStopUSec','--value')
    if str(SRC/'meteor_morning.py') not in unit or '--phase analyze' not in unit:
        raise RuntimeError('EXPECTED_RC15_ANALYZER_MISSING')
    if 'after_analysis_rc151.py' not in post or timeout!='20min':
        raise RuntimeError('RC151_REPORT_OR_TIMEOUT_MISSING')
    mor=cmd('systemctl','--user','show',MORNING,'-p','ExecStart','--value')
    if str(SRC/'meteor_morning.py') not in mor or '--phase sync' not in mor:
        raise RuntimeError('MORNING_SYNC_BASELINE_MISMATCH')
    # Manifest is an immutable record of the RC1.5 source, not an install permission.
    manifest=SRC.parent/'MANIFEST.json'
    if not manifest.is_file() or manifest.is_symlink():raise RuntimeError('RC15_MANIFEST_MISSING')
    records=json.loads(manifest.read_text())
    for file in ('MeteorHub/meteor_morning.py','MeteorHub/hub_workflow.py',
                 'MeteorHub/night_scope.py','MeteorHub/storage_guard.py'):
        expected=records.get('files_sha256',{}).get(file)
        actual=hashlib.sha256((SRC/file.split('/',1)[1]).read_bytes()).hexdigest()
        if not expected or actual != expected:raise RuntimeError('RC15_SOURCE_HASH_MISMATCH: '+file)
    return unit


def stage(unit):
    # A versioned release is prepared away from running services.
    ROOT.mkdir(parents=True,mode=0o700)
    for root, dirs, files in os.walk(SRC):
        for name in dirs+files:
            if (Path(root)/name).is_symlink():raise RuntimeError('UNSAFE_SOURCE_SYMLINK')
    shutil.copytree(SRC,DST,symlinks=False,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    source=(DST/'meteor_morning.py').read_text(encoding='utf-8')
    if source.count(ANCHOR)!=1:raise RuntimeError('UNEXPECTED_RECLAIM_ANCHOR')
    updated=source.replace(ANCHOR,REPLACEMENT,1)
    ast.parse(updated,filename='meteor_morning.py')
    ast.parse(RECOVERY,filename='recover_policy.py')
    ast.parse(TEST,filename='test_recover_policy.py')
    (DST/'meteor_morning.py').write_text(updated)
    (DST/'recover_policy.py').write_text(RECOVERY)
    (DST/'test_recover_policy.py').write_text(TEST)
    test=subprocess.run(['/usr/bin/python3','-B','-m','unittest','-q','test_recover_policy'],
         cwd=DST,capture_output=True,text=True,timeout=20)
    if test.returncode or 'Ran 6 tests' not in test.stdout+test.stderr:
        raise RuntimeError('RC152_UNIT_TEST_FAILURE: '+(test.stdout+test.stderr)[-280:])
    files={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
           for p in DST.glob('*.py')}
    (ROOT/'RC152-MANIFEST.json').write_text(json.dumps({
       'schema':'meteormesh.rc152.bounded-retry.v1',
       'retries_per_job_max':1,'maximum_claim_attempts':2,
       'per_night_scope_required':True,'old_source_kept':True,
       'auto_deletion':False,'cloud_upload':False,
       'files_sha256':files},indent=2,sort_keys=True)+'\n')
    oldscript=str(SRC/'meteor_morning.py')
    # Systemctl's show format can contain ExecStart metadata; use known drop-in
    # instead of parsing an opaque printable argv[] representation.
    old_drop=HOME/'.config/systemd/user/meteormesh-analyze.service.d/99-meteormesh-rc15.conf'
    if not old_drop.is_file() or old_drop.is_symlink():raise RuntimeError('RC15_UNIT_DROP_MISSING')
    lines=[s.partition('=')[2].strip() for s in old_drop.read_text().splitlines() if s.startswith('ExecStart=') and s.partition('=')[2].strip()]
    if len(lines)!=1:raise RuntimeError('RC15_UNIT_EXEC_NOT_SINGLE')
    a=shlex.split(lines[0])
    if a.count(oldscript)!=1 or '--phase' not in a or a[a.index('--phase')+1]!='analyze':
        raise RuntimeError('RC15_ANALYZER_COMMAND_MISMATCH')
    a[a.index(oldscript)]=str(DST/'meteor_morning.py')
    data='[Service]\nExecStart=\nExecStart='+shlex.join(a)+'\n'
    DROP.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=DROP.parent,prefix='.rc152-')
    try:
        with os.fdopen(fd,'w') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o644)
        os.replace(tmp,DROP)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def main():
    unit=assert_baseline()
    try:
        stage(unit)
        assert_idle()
        cmd('systemctl','--user','daemon-reload')
        active=cmd('systemctl','--user','show',SERVICE,'-p','ExecStart','--value')
        post=cmd('systemctl','--user','show',SERVICE,'-p','ExecStopPost','--value')
        mor=cmd('systemctl','--user','show',MORNING,'-p','ExecStart','--value')
        if (str(DST/'meteor_morning.py') not in active or '--phase analyze' not in active
            or str(SRC/'meteor_morning.py') not in mor or '--phase sync' not in mor
            or 'after_analysis_rc151.py' not in post):
            raise RuntimeError('RC152_EFFECTIVE_SETTINGS_MISMATCH')
        assert_idle()
    except BaseException:
        DROP.unlink(missing_ok=True)
        subprocess.run(['systemctl','--user','daemon-reload'],capture_output=True,timeout=15)
        if ROOT.is_dir() and not ROOT.is_symlink():shutil.rmtree(ROOT)
        raise
    print('RC152_BOUNDED_QUEUE: PASS')
    print('FAILED_RETRY: ONE_ADDITIONAL_ATTEMPT_ON_NEXT_ANALYSIS')
    print('STALE_RUN: AT_MOST_TWO_ATTEMPTS')
    print('NIGHT_SCOPE: RESPECTED; THREE_CAMERAS_INDEPENDENT')
    print('MORNING_SYNC: RC15_UNCHANGED')
    print('REPORT: RC151_UNCHANGED; STOP_TIMEOUT: 20min')
    print('VIDEO_DB_TIMERS_AND_SERVICE_PROCESS: NOT_TOUCHED')
    print('ROLLBACK: rm '+str(DROP)+' && systemctl --user daemon-reload')

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('RC152_STOPPED: '+type(e).__name__+': '+str(e)[:220],file=sys.stderr)
        sys.exit(2)
