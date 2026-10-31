#!/usr/bin/env python3
"""MeteorMesh RC1.5.3: verified SQLite backup to independent SSD, no new timer.

Installs versioned helper and one post-analysis wrapper. Keeps the existing RC1.5.1
observer; backs up to /home after the nightly analysis, never modifies source rows,
original MP4, timers, or retention. Takes first verified backup at installation.
"""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

HOME=Path.home()
ROOT=HOME/'.local/share/meteormesh/releases/rc153-20261101-backup'
DB_TOOL=ROOT/'meteor_db_backup.py'
CHAIN=ROOT/'after_analysis_with_backup.py'
MANIFEST=ROOT/'RC153-MANIFEST.json'
BACKUPS=HOME/'.local/share/meteormesh/db-backups'
DROP=HOME/'.config/systemd/user/meteormesh-analyze.service.d/zzzz-rc153-backup.conf'
BASE_WRAPPER=HOME/'.local/share/meteormesh/releases/rc15-20261031-consolidated/hooks/after_analysis_rc151.py'
UNIT='meteormesh-analyze.service'
UUID='444a8a6d-090a-456c-8a9d-d99665c32574'

BACKUP=r'''#!/usr/bin/env python3
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
'''

WRAPPER=r'''#!/usr/bin/env python3
"""RC1.5.3 single post-analysis chain; preserves existing observer and SSD backup."""
from pathlib import Path
import subprocess
import sys

HOME=Path.home()
ORIGINAL=HOME/'.local/share/meteormesh/releases/rc15-20261031-consolidated/hooks/after_analysis_rc151.py'
BACKUP=Path(__file__).resolve().parent/'meteor_db_backup.py'

def main():
    for label,script,timeout in (('REPORT',ORIGINAL,900),('DB_BACKUP',BACKUP,180)):
        try:
            r=subprocess.run(['/usr/bin/python3','-B',str(script)],capture_output=True,text=True,timeout=timeout)
            for line in r.stdout.splitlines():print(line,flush=True)
            if r.stderr:print('RC153_CHAIN_STDERR: '+label+' '+r.stderr[:200].replace('\n',' '),flush=True)
            status='OK' if r.returncode==0 else 'ERROR_EXIT_'+str(r.returncode)
            if r.returncode==0 and label=='REPORT':
                summaries=[line for line in r.stdout.splitlines() if line.startswith('RC151_AFTER_ANALYSIS: ')]
                if len(summaries)!=1:status='ERROR_MISSING_REPORT_SUMMARY'
                elif 'ERROR_' in summaries[0]:status='WARNING_REPORT_ERRORS'
                elif 'WARNING_' in summaries[0]:status='WARNING_PARTIAL_REPORT'
            if r.returncode==0 and label=='DB_BACKUP' and 'MM153_DB_BACKUP: PASS' not in r.stdout:
                status='ERROR_MISSING_BACKUP_RESULT'
        except (OSError,subprocess.TimeoutExpired) as e:
            status='ERROR_'+type(e).__name__
        print('RC153_CHAIN: '+label+'='+status,flush=True)
    return 0  # Observer does not mask detector result.

if __name__=='__main__':sys.exit(main())
'''

def shell(*a,check=True):
    p=subprocess.run(a,capture_output=True,text=True,timeout=15)
    if check and p.returncode:raise RuntimeError('FAILED: '+' '.join(a)+' '+p.stderr.strip()[:160])
    return p.stdout.strip()

def require_baseline():
    if os.geteuid()==0:raise RuntimeError('NOT_ROOT')
    for name in ('meteormesh-morning.service',UNIT):
        status=shell('systemctl','--user','is-active',name,check=False)
        if status not in ('inactive','failed'):
            raise RuntimeError('SERVICE_BUSY_OR_UNKNOWN: '+name+':'+status)
    if ROOT.exists() or ROOT.is_symlink() or DROP.exists() or DROP.is_symlink():
        raise RuntimeError('RC153_TARGET_EXISTS')
    if not BASE_WRAPPER.is_file() or BASE_WRAPPER.is_symlink():raise RuntimeError('RC151_HOOK_MISSING')
    effective=shell('systemctl','--user','show',UNIT,'-p','ExecStopPost','--value')
    start=shell('systemctl','--user','show',UNIT,'-p','ExecStart','--value')
    if str(BASE_WRAPPER) not in effective or 'rc152-20261101-bounded-retry' not in start:
        raise RuntimeError('RC152_BASELINE_NOT_ACTIVE')
    if shell('systemctl','--user','show',UNIT,'-p','TimeoutStopUSec','--value')!='20min':
        raise RuntimeError('RC151_TIMEOUT_NOT_FOUND')
    if shell('systemctl','--user','is-enabled','meteormesh-morning.timer',check=False)!='enabled':
        raise RuntimeError('MORNING_TIMER_NOT_ENABLED')
    return True

def atomic_write(path,blob,mode):
    if path.exists() or path.is_symlink():raise RuntimeError('TARGET_EXISTS: '+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(blob);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,mode)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def main():
    require_baseline()
    ast.parse(BACKUP,filename='meteor_db_backup.py')
    ast.parse(WRAPPER,filename='after_analysis_with_backup.py')
    ROOT.mkdir(parents=True,mode=0o700)
    drop=('[Service]\nExecStopPost=\nExecStopPost=/usr/bin/python3 -B '+str(CHAIN)+'\n').encode()
    try:
        atomic_write(DB_TOOL,BACKUP.encode(),0o600)
        atomic_write(CHAIN,WRAPPER.encode(),0o600)
        hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (DB_TOOL,CHAIN)}
        atomic_write(MANIFEST,(json.dumps({'schema':'meteormesh.rc153.db-backup.v1',
            'ssd_destination':str(BACKUPS),'db_only':True,'automatic_delete':False,
            'cloud_upload':False,'after_analysis_existing_report_preserved':True,
            'sha256':hashes},indent=2,sort_keys=True)+'\n').encode(),0o600)
        # Create verified first SSD backup before changing the service hook.
        tested=subprocess.run(['/usr/bin/python3','-B',str(DB_TOOL)],capture_output=True,text=True,timeout=180)
        if tested.returncode or 'MM153_DB_BACKUP: PASS' not in tested.stdout:
            raise RuntimeError('FIRST_BACKUP_FAILED: '+(tested.stdout+' '+tested.stderr)[-280:])
        for line in tested.stdout.splitlines():print(line,flush=True)
        atomic_write(DROP,drop,0o644)
        shell('systemctl','--user','daemon-reload')
        current=shell('systemctl','--user','show',UNIT,'-p','ExecStopPost','--value')
        if current.count(str(CHAIN))!=1 or str(BASE_WRAPPER) in current:
            raise RuntimeError('NEW_BACKUP_CHAIN_NOT_EFFECTIVE')
        if shell('systemctl','--user','show',UNIT,'-p','TimeoutStopUSec','--value')!='20min':
            raise RuntimeError('TIMEOUT_CHANGED')
    except BaseException:
        DROP.unlink(missing_ok=True)
        subprocess.run(['systemctl','--user','daemon-reload'],capture_output=True,timeout=10)
        if ROOT.is_dir() and not ROOT.is_symlink():shutil.rmtree(ROOT)
        # Verified SSD backup is deliberately kept if produced successfully.
        raise
    print('RC153_SSD_BACKUP: PASS')
    print('FIRST_SQLITE_BACKUP: VERIFIED_ON_SSD')
    print('DAILY: AFTER_ANALYSIS_IN_EXISTING_CHAIN')
    print('RAW_VIDEO_DELETE_CLOUD_TIMER: UNCHANGED')
    print('ROLLBACK: rm '+str(DROP)+' && systemctl --user daemon-reload')

if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,sqlite3.Error,subprocess.SubprocessError,ValueError,SyntaxError) as exc:
        print('RC153_STOPPED: '+type(exc).__name__+': '+str(exc)[:240],file=sys.stderr)
        sys.exit(2)
