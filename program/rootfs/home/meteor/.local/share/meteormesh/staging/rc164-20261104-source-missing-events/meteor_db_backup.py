#!/usr/bin/env python3
"""Create, verify and retain independent SSD SQLite backups, fail closed on HDD loss.

The online backup API obtains one consistent database snapshot, including
committed WAL transactions. Does not copy live .sqlite3 files directly.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid

HOME=Path.home()
DATA=Path('/srv/meteor-data')
SOURCE=DATA/'meteorhub.sqlite3'
OUTPUT=HOME/'.local/share/meteormesh/db-backups'
EXPECTED='444a8a6d-090a-456c-8a9d-d99665c32574'
MAX_BYTES=1024**3  # Unexpectedly large DB requires investigation, not silent SSD filling.
MIN_FREE=2*1024**3


def verified_source():
    if DATA.is_symlink() or not DATA.is_dir() or SOURCE.is_symlink() or not SOURCE.is_file():
        raise RuntimeError('HDD_OR_SQLITE_MISSING_OR_SYMLINK')
    proc=subprocess.run(['findmnt','-n','-o','UUID,FSTYPE','--mountpoint',str(DATA)],
                        text=True,capture_output=True,timeout=10)
    if proc.returncode or proc.stdout.split()!=[EXPECTED,'ext4']:
        raise RuntimeError('HDD_MOUNT_UUID_UNVERIFIED')
    s=SOURCE.stat()
    if not stat.S_ISREG(s.st_mode) or s.st_dev!=DATA.stat().st_dev or not (0<s.st_size<MAX_BYTES):
        raise RuntimeError('SQLITE_NOT_ON_VERIFIED_HDD_OR_SIZE_UNSAFE')
    if not HOME.is_dir() or HOME.stat().st_dev==DATA.stat().st_dev:
        raise RuntimeError('BACKUP_TARGET_NOT_INDEPENDENT_DISK')
    return s


def safe_output():
    parent=HOME/'.local/share/meteormesh'
    if parent.is_symlink() or not parent.is_dir():
        raise RuntimeError('METEORMESH_CONFIG_ROOT_UNSAFE')
    if OUTPUT.is_symlink():
        raise RuntimeError('BACKUP_DIRECTORY_SYMLINK')
    if not OUTPUT.exists():
        OUTPUT.mkdir(mode=0o700)
    st=OUTPUT.stat()
    if (not OUTPUT.is_dir() or st.st_uid!=os.getuid() or st.st_mode & 0o077
          or st.st_dev!=HOME.stat().st_dev):
        raise RuntimeError('BACKUP_DIRECTORY_UNSAFE')
    v=os.statvfs(OUTPUT)
    if v.f_bavail*v.f_frsize < MIN_FREE+min(SOURCE.stat().st_size*3,MAX_BYTES*2):
        raise RuntimeError('SSD_SPACE_INSUFFICIENT')


def hash_file(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        while True:
            chunk=f.read(1024*1024)
            if not chunk:break
            h.update(chunk)
    return h.hexdigest()


def make_backup():
    if os.geteuid()==0:raise RuntimeError('DO_NOT_RUN_AS_ROOT')
    verified_source()
    safe_output()
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    tag='meteorhub-'+stamp+'-'+uuid.uuid4().hex[:8]
    destination=OUTPUT/(tag+'.sqlite3')
    receipt=OUTPUT/(tag+'.json')
    fd,tmp=tempfile.mkstemp(prefix='.mmdb-',suffix='.sqlite3',dir=OUTPUT)
    os.chmod(tmp,0o600)
    try:
        os.close(fd)
        start=time.monotonic()
        with sqlite3.connect(SOURCE.as_uri()+'?mode=ro',uri=True,timeout=5) as source:
            source.execute('PRAGMA query_only=ON')
            with sqlite3.connect(tmp,timeout=5) as target:
                def progress(status,left,total):
                    if time.monotonic()-start>120:
                        raise TimeoutError('BACKUP_DEADLINE_EXCEEDED')
                source.backup(target,pages=256,sleep=0.1,progress=progress)
                target.commit()
        verified_source()
        with sqlite3.connect('file:'+tmp+'?mode=ro',uri=True,timeout=5) as check:
            result=check.execute('PRAGMA quick_check').fetchone()
            if result!=('ok',):raise RuntimeError('BACKUP_SQLITE_QUICK_CHECK_FAILED')
            table_names={r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'jobs','segments','detector_runs'}<=table_names:
                raise RuntimeError('BACKUP_TABLES_MISSING')
            jobs=check.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        file_size=os.stat(tmp).st_size
        if file_size<=0 or file_size>=MAX_BYTES:
            raise RuntimeError('BACKUP_FILE_SIZE_UNSAFE')
        sha=hash_file(Path(tmp))
        os.replace(tmp,destination)
        # An unexpected receipt-write failure leaves the verified SQLite intact.
        metadata={'schema':'meteormesh.db-backup.v1','created_utc':stamp,
                  'source_uuid':EXPECTED,'source':'/srv/meteor-data/meteorhub.sqlite3',
                  'backup_file':destination.name,'sha256':sha,'bytes':file_size,
                  'quick_check':'ok','job_rows':jobs,'raw_videos_copied':False,
                  'ssd_storage_independent':True,'originals_deleted':False}
        receipt_fd,receipt_tmp=tempfile.mkstemp(prefix='.mmreceipt-',dir=OUTPUT)
        try:
            with os.fdopen(receipt_fd,'w') as f:
                os.fchmod(f.fileno(),0o600)
                json.dump(metadata,f,sort_keys=True)
                f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(receipt_tmp,receipt)
        finally:
            if os.path.exists(receipt_tmp):os.unlink(receipt_tmp)
        print('MM153_DB_BACKUP: PASS',flush=True)
        print('BACKUP_PATH: '+str(destination),flush=True)
        print('SHA256: '+sha,flush=True)
        print('JOB_ROWS: '+str(jobs)+'; QUICK_CHECK: ok; SOURCE_UNMODIFIED',flush=True)
        return 0
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

if __name__=='__main__':
    try:sys.exit(make_backup())
    except (OSError,RuntimeError,sqlite3.Error,subprocess.SubprocessError,TimeoutError) as e:
        print('MM153_DB_BACKUP: ERROR '+type(e).__name__+': '+str(e)[:170],flush=True)
        sys.exit(2)
