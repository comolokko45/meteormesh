#!/usr/bin/env python3
"""RC1.5.5: expand analysis budget only; preserve morning, DB, footage, timers.

360 five-minute segments at 127.9 s/segment need ~12h47m CPU; raise bounded
analysis budget from 12 to 16h, systemd start timeout 13 to 18h. No worker
parallelism, no retries beyond RC1.5.2 and no data migration.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

HOME=Path.home()
SERVICE='meteormesh-analyze.service'
MORNING='meteormesh-morning.service'
SRC=HOME/'.config/systemd/user/meteormesh-analyze.service.d/zzz-rc152-bounded-queue.conf'
DEST=HOME/'.config/systemd/user/meteormesh-analyze.service.d/zzzzz-rc155-capacity.conf'
HUB=HOME/'.local/share/meteormesh/releases/rc152-20261101-bounded-retry/MeteorHub/meteor_morning.py'
BACKUP=HOME/'.local/share/meteormesh/releases/rc155-capacity-20261101'


def run(*args, check=True):
    p=subprocess.run(args, capture_output=True, text=True, timeout=15)
    if check and p.returncode:
        raise RuntimeError('FAILED: '+' '.join(args[:5])+': '+p.stderr[:160])
    return p.stdout.strip()


def need(test, msg):
    if not test: raise RuntimeError(msg)


def idle():
    for unit in (SERVICE, MORNING):
        state=run('systemctl','--user','is-active',unit,check=False)
        need(state in ('inactive','failed'), 'UNIT_NOT_IDLE: '+unit+': '+state)


def write_new(path, content, mode):
    need(not path.exists() and not path.is_symlink(),'DESTINATION_ALREADY_PRESENT: '+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(content);f.flush();os.fsync(f.fileno())
        os.chmod(temp,mode)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def assert_base():
    need(os.geteuid()!=0,'NOT_AS_ROOT')
    idle()
    need(SRC.is_file() and not SRC.is_symlink(),'RC152_OVERRIDE_NOT_FOUND')
    need(HUB.is_file() and not HUB.is_symlink(),'RC152_HUB_NOT_FOUND')
    need(not DEST.exists() and not DEST.is_symlink(),'ALREADY_STAGED')
    need(not BACKUP.exists() and not BACKUP.is_symlink(),'RELEASE_ALREADY_STAGED')
    current=run('systemctl','--user','show',SERVICE,'-p','ExecStart','--value')
    post=run('systemctl','--user','show',SERVICE,'-p','ExecStopPost','--value')
    starttimeout=run('systemctl','--user','show',SERVICE,'-p','TimeoutStartUSec','--value')
    stoptimeout=run('systemctl','--user','show',SERVICE,'-p','TimeoutStopUSec','--value')
    morning=run('systemctl','--user','show',MORNING,'-p','ExecStart','--value')
    need(str(HUB) in current and '--phase analyze' in current,'RC152_EXEC_NOT_ACTIVE')
    need('rc153-20261101-backup/after_analysis_with_backup.py' in post,'RC153_BACKUP_HOOK_MISSING')
    need(starttimeout=='13h','UNEXPECTED_TIMEOUT_START: '+starttimeout)
    need(stoptimeout=='20min','UNEXPECTED_TIMEOUT_STOP: '+stoptimeout)
    need('rc15-20261031-consolidated/MeteorHub/meteor_morning.py' in morning and '--phase sync' in morning,
         'MORNING_PHASE_CHANGED')
    lines=[s.strip().partition('=')[2] for s in SRC.read_text().splitlines()
           if s.strip().startswith('ExecStart=') and s.strip().partition('=')[2]]
    need(len(lines)==1,'EXPECTED_ONE_EXECSTART')
    parts=shlex.split(lines[0])
    need(parts[:2]==['/usr/bin/python3','-B'] and parts.count(str(HUB))==1,
         'ANALYZE_SCRIPT_MISMATCH')
    need(parts.count('--phase')==1 and parts[parts.index('--phase')+1]=='analyze',
         'ANALYZE_PHASE_MISMATCH')
    need(parts.count('--max-runtime-hours')==1 and
         parts[parts.index('--max-runtime-hours')+1]=='12', 'UNEXPECTED_RUNTIME_BASELINE')
    need(parts.count('--max-jobs')==1 and parts[parts.index('--max-jobs')+1]=='500',
         'UNEXPECTED_JOB_LIMIT')
    return parts


def main():
    args=assert_base()
    args[args.index('--max-runtime-hours')+1]='16'
    conf=('[Service]\nExecStart=\nExecStart='+shlex.join(args)+
          '\nTimeoutStartSec=18h\n').encode()
    readme=('MeteorMesh RC1.5.5 bounded analysis capacity\n'
            'Analysis: 16h max-runtime, 18h systemd start timeout, 500 jobs\n'
            'Morning ingest/sync remains 12h, no camera/timer modifications.\n'
            'Rollback: remove zzzzz-rc155-capacity.conf then daemon-reload.\n'
            'No automatic deletion or cloud actions.\n').encode()
    items=[(DEST,conf,0o644),(BACKUP/'RC155-CAPACITY.txt',readme,0o600)]
    created=[]
    try:
        idle()
        for path,data,mode in items:
            write_new(path,data,mode)
            created.append(path)
        run('systemctl','--user','daemon-reload')
        effective=run('systemctl','--user','show',SERVICE,'-p','ExecStart','--value')
        starttimeout=run('systemctl','--user','show',SERVICE,'-p','TimeoutStartUSec','--value')
        stoptimeout=run('systemctl','--user','show',SERVICE,'-p','TimeoutStopUSec','--value')
        post=run('systemctl','--user','show',SERVICE,'-p','ExecStopPost','--value')
        need(str(HUB) in effective and '--max-runtime-hours 16' in effective,
             'CAPACITY_NOT_EFFECTIVE')
        need(starttimeout=='18h' and stoptimeout=='20min', 'TIMEOUT_NOT_EFFECTIVE')
        need('rc153-20261101-backup/after_analysis_with_backup.py' in post,
             'BACKUP_HOOK_LOST')
        idle()
    except BaseException:
        for path in reversed(created):path.unlink(missing_ok=True)
        if BACKUP.is_dir() and not BACKUP.is_symlink(): BACKUP.rmdir()
        subprocess.run(['systemctl','--user','daemon-reload'],capture_output=True,timeout=15)
        raise
    print('RC155_CAPACITY: PASS')
    print('ANALYZE_BUDGET: 16h; START_TIMEOUT: 18h')
    print('MAX_JOBS: 500; ONE_WORKER; STOP_TIMEOUT: 20min')
    print('MORNING_SYNC_APK_SQLITE_HDD_TIMERS: UNCHANGED')
    print('RC151_REPORT_RC152_RECOVERY_RC153_BACKUP: PRESERVED')
    print('NO_ANALYSIS_STARTED; AUTO_DELETE_AND_CLOUD: OFF')
    print('ROLLBACK: rm '+str(DEST)+' && systemctl --user daemon-reload')


if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,subprocess.SubprocessError) as e:
        print('RC155_STOPPED: '+type(e).__name__+': '+str(e)[:210],file=sys.stderr)
        sys.exit(2)
