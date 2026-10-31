#!/usr/bin/env python3
"""MeteorMesh RC1.5.4: offline restore drill and configuration acceptance audit.

Never writes to production SQLite, video files, systemd units, or timers.
Temporarily restores the latest verified SSD backup to a separate SSD file,
performs SQLite full integrity checks, then deletes ONLY this temporary file.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile

HOME=Path.home()
RELEASE=HOME/'.local/share/meteormesh/releases'
R15=RELEASE/'rc15-20261031-consolidated'
R152=RELEASE/'rc152-20261101-bounded-retry'
R153=RELEASE/'rc153-20261101-backup'
BACKUPS=HOME/'.local/share/meteormesh/db-backups'
DATA=Path('/srv/meteor-data')
UUID='444a8a6d-090a-456c-8a9d-d99665c32574'
BACKUP_RE=re.compile(r'meteorhub-\d{8}T\d{6}Z-[0-9a-f]{8}\.json\Z')
HEXDIGEST=re.compile(r'[0-9a-f]{64}\Z')


def need(test,msg):
    if not test:raise RuntimeError(msg)


def regular(path, maxbytes=None):
    need(not path.is_symlink() and path.is_file(),'MISSING_OR_SYMLINK: '+str(path))
    s=path.stat()
    need(stat.S_ISREG(s.st_mode),'NOT_REGULAR_FILE')
    if maxbytes is not None:need(0<s.st_size<=maxbytes,'BAD_FILE_SIZE: '+path.name)
    return s


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while chunk:=f.read(1024*1024):h.update(chunk)
    return h.hexdigest()


def shell(*args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=12)
    need(p.returncode==0,'SYSTEMCTL_OR_MOUNT_COMMAND_FAILED: '+' '.join(args[:4]))
    return p.stdout.strip()


def assert_mount():
    need(not DATA.is_symlink() and DATA.is_dir(),'HDD_MOUNT_FOLDER_INVALID')
    actual=shell('findmnt','-n','-o','UUID,FSTYPE','--mountpoint',str(DATA)).split()
    need(actual==[UUID,'ext4'],'HDD_UUID_OR_FILESYSTEM_MISMATCH')
    need(DATA.stat().st_dev!=HOME.stat().st_dev,'HDD_AND_SSD_NOT_INDEPENDENT')
    free=os.statvfs(DATA)
    gib=free.f_bavail*free.f_frsize/1024**3
    need(gib>=100,'HDD_BELOW_100_GIB_RESERVE')
    print('HDD_GUARD: OK; FREE_GIB: %.1f'%gib,flush=True)
    if gib<150:print('DISK_WARNING: BELOW_150_GIB',flush=True)
    print('RETENTION: NO_AUTOMATIC_VIDEO_DELETE',flush=True)


def assert_manifest():
    base=json.loads((R15/'MANIFEST.json').read_text())
    need(base.get('schema')=='meteormesh.rc15.consolidated.v1','BAD_RC15_MANIFEST')
    for rel in ('MeteorHub/meteor_morning.py','MeteorHub/night_scope.py',
                'MeteorHub/hub_workflow.py','MeteorHub/storage_guard.py'):
        p=R15/rel
        regular(p,200000)
        need(base.get('files_sha256',{}).get(rel)==digest(p),'RC15_CODE_CHANGED: '+rel)
    rc152=json.loads((R152/'RC152-MANIFEST.json').read_text())
    need(rc152.get('schema')=='meteormesh.rc152.bounded-retry.v1','BAD_RC152_MANIFEST')
    for rel in ('MeteorHub/meteor_morning.py','MeteorHub/recover_policy.py','MeteorHub/storage_guard.py'):
        p=R152/rel
        regular(p,200000)
        need(rc152.get('files_sha256',{}).get(rel)==digest(p),'RC152_CODE_CHANGED: '+rel)
    rc153=json.loads((R153/'RC153-MANIFEST.json').read_text())
    need(rc153.get('schema')=='meteormesh.rc153.db-backup.v1','BAD_RC153_MANIFEST')
    for rel in ('meteor_db_backup.py','after_analysis_with_backup.py'):
        p=R153/rel
        regular(p,200000)
        need(rc153.get('sha256',{}).get(rel)==digest(p),'RC153_CODE_CHANGED: '+rel)
    policy=HOME/'.local/share/meteormesh/staging/fullnight/meteor-auto-night-policy.py'
    text=policy.read_text()
    need(all(x in text for x in ('dt.time(5,0)','19:00-05:00','10*3600*1000')),'NIGHT_POLICY_NOT_19_05')
    print('VERSIONED_CODE: RC15_RC152_RC153_HASH_OK',flush=True)
    print('NIGHT_POLICY: 19:00-05:00',flush=True)


def assert_service():
    analysis=shell('systemctl','--user','show','meteormesh-analyze.service','-p','ExecStart','--value')
    post=shell('systemctl','--user','show','meteormesh-analyze.service','-p','ExecStopPost','--value')
    timeout=shell('systemctl','--user','show','meteormesh-analyze.service','-p','TimeoutStopUSec','--value')
    morning=shell('systemctl','--user','show','meteormesh-morning.service','-p','ExecStart','--value')
    pre=shell('systemctl','--user','show','meteormesh-morning.service','-p','ExecStartPre','--value')
    trigger=shell('systemctl','--user','show','meteormesh-morning.service','-p','OnSuccess','--value')
    need(str(R152/'MeteorHub/meteor_morning.py') in analysis and '--phase analyze' in analysis,
         'RC152_ANALYSIS_NOT_EFFECTIVE')
    need(str(R153/'after_analysis_with_backup.py') in post,'RC153_BACKUP_CHAIN_NOT_EFFECTIVE')
    need(timeout=='20min','STOP_TIMEOUT_NOT_20MIN')
    need(str(R15/'MeteorHub/meteor_morning.py') in morning and '--phase sync' in morning,
         'RC15_MORNING_SYNC_NOT_EFFECTIVE')
    need('mm01_readonly_preflight.py' in pre and 'meteor-auto-night-policy.py' in pre,
         'MM01_OR_NIGHT_POLICY_PRESTART_MISSING')
    need('meteormesh-analyze.service' in trigger,'MORNING_TO_ANALYSIS_TRIGGER_MISSING')
    need(shell('systemctl','--user','is-enabled','meteormesh-morning.timer')=='enabled',
         'MORNING_TIMER_DISABLED')
    print('SYSTEMD_CHAIN: SYNC_RC15_ANALYZE_RC152_BACKUP_RC153',flush=True)
    print('TIMEOUT_STOP: 20min; TIMER: ENABLED',flush=True)


def verified_latest():
    need(BACKUPS.is_dir() and not BACKUPS.is_symlink(),'SSD_BACKUP_DIRECTORY_MISSING')
    bstat=BACKUPS.stat()
    need(bstat.st_uid==os.getuid() and not bstat.st_mode&0o077,'BACKUP_DIRECTORY_PERMISSIONS')
    need(bstat.st_dev==HOME.stat().st_dev,'BACKUPS_NOT_ON_SSD')
    items=sorted((p for p in BACKUPS.iterdir() if BACKUP_RE.fullmatch(p.name)),reverse=True)
    need(bool(items),'NO_BACKUP_RECEIPTS')
    receipt=items[0]
    regular(receipt,16384)
    need(not receipt.stat().st_mode&0o077,'BACKUP_RECEIPT_PERMISSIONS')
    info=json.loads(receipt.read_text())
    need(info.get('schema')=='meteormesh.db-backup.v1','BACKUP_RECEIPT_SCHEMA')
    need(info.get('source_uuid')==UUID,'BACKUP_RECEIPT_UUID')
    name=info.get('backup_file')
    need(isinstance(name,str) and name==receipt.stem+'.sqlite3','BACKUP_RECEIPT_NAME_MISMATCH')
    backup=BACKUPS/name
    bs=regular(backup,1024**3)
    need(bs.st_dev==HOME.stat().st_dev,'BACKUP_NOT_ON_SSD')
    need(not bs.st_mode&0o077,'BACKUP_PERMISSIONS_UNSAFE')
    need(bs.st_size==info.get('bytes'),'BACKUP_SIZE_MISMATCH')
    need(HEXDIGEST.fullmatch(info.get('sha256','')) is not None,'BACKUP_BAD_DIGEST_FORMAT')
    need(digest(backup)==info['sha256'],'BACKUP_SHA256_MISMATCH')
    print('BACKUP_SHA256: VERIFIED',flush=True)
    return backup,info


def drill(backup,receipt):
    # Only temporary restored copy is written; no write handle to production DB.
    fd,name=tempfile.mkstemp(prefix='.restore-drill-',suffix='.sqlite3',dir=BACKUPS)
    os.close(fd)
    os.chmod(name,0o600)
    tmp=Path(name)
    try:
        with sqlite3.connect(backup.as_uri()+'?mode=ro',uri=True,timeout=5) as source:
            source.execute('PRAGMA query_only=ON')
            with sqlite3.connect(tmp,timeout=5) as target:
                source.backup(target,pages=256,sleep=0.1)
        with sqlite3.connect(tmp.as_uri()+'?mode=ro',uri=True,timeout=5) as db:
            db.execute('PRAGMA query_only=ON')
            integrity=db.execute('PRAGMA integrity_check').fetchall()
            need(integrity==[('ok',)],'RESTORE_INTEGRITY_CHECK_FAILED')
            tables={v[0] for v in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            need({'jobs','segments','detector_runs'}<=tables,'RESTORED_REQUIRED_TABLE_MISSING')
            jobs=db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
            need(jobs==receipt.get('job_rows'),'RESTORED_JOB_ROWS_MISMATCH')
            print('RESTORE_DRILL: PASS; RESTORED_JOB_ROWS: '+str(jobs),flush=True)
    finally:
        tmp.unlink(missing_ok=True)
    print('RESTORE_TMP_REMOVED: YES; PRODUCTION_DATABASE: UNTOUCHED',flush=True)


def main():
    need(os.geteuid()!=0,'DO_NOT_RUN_AS_ROOT')
    assert_mount()
    assert_manifest()
    assert_service()
    backup,receipt=verified_latest()
    drill(backup,receipt)
    print('RC154_LINUX_RELEASE_AUDIT: PASS',flush=True)
    print('FIELD_10H_ACCEPTANCE: STILL_REQUIRED',flush=True)
    print('APK_013_AND_BACKLOG_CAPACITY: OUTSIDE_THIS_AUDIT',flush=True)

if __name__=='__main__':
    try:main()
    except (RuntimeError,ValueError,OSError,sqlite3.Error,subprocess.SubprocessError,json.JSONDecodeError) as e:
        print('RC154_AUDIT_STOPPED: '+type(e).__name__+': '+str(e)[:180],file=sys.stderr)
        sys.exit(2)
