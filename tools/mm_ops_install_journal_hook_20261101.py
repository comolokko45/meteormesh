#!/usr/bin/env python3
"""Reversible, fail-closed RC1.5 ops journal hook installer.

Add one nonblocking ExecStartPost to existing meteormesh-status.service.
Uses the existing 30-minute timer and keeps the heartbeat hook untouched.
Does not start/stop units, update video/database, contact phones, change HDD,
install additional timers, create token/secret or send external notifications.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from urllib.request import urlopen

HOME=Path.home()
BASE=HOME/'.local/lib/meteormesh'
DROP=HOME/'.config/systemd/user/meteormesh-status.service.d/zz-meteor-ops-local.conf'
SERVICE='meteormesh-status.service'
TIMER='meteormesh-status.timer'
SRC={
 'meteor-ops-summary.py':(
   'ff6fb8b97ac01e3890bddd4a46fcbe901dec4725',
   'mm_ops_summary_preview_20261101.py',
   '912b8ab9efe63f6c2f3db9954571850619b558e6'),
 'meteor-ops-local-alerts.py':(
   '3935ae1ec167243c044036a3c6e18e9a4dc308e5',
   'mm_ops_local_alerts_20261101.py',
   '6def69efe0f3ccfad3b9a6823d65e180247137c3'),
 'meteor-ops-journal.py':(
   'd293ca55639e6a3d5fffb2c6ad30f728caa59055',
   'mm_ops_journal_hook_20261101.py',
   'aa022fac4b9f976436f42c357947785cb3c2c48b')}

def systemctl(*args):
    p=subprocess.run(['systemctl','--user',*args],capture_output=True,
                     text=True,timeout=18)
    if p.returncode:
        raise RuntimeError('SYSTEMCTL_'+args[0].upper()+'_FAILED: '+(p.stderr or p.stdout)[:100])
    return p.stdout.strip()

def before():
    if os.geteuid()==0:raise RuntimeError('NOT_ROOT')
    for dest in [*(BASE/name for name in SRC), DROP]:
        if dest.exists() or dest.is_symlink():
            raise RuntimeError('EXISTING_DESTINATION_PROTECTED: '+str(dest))
        if not dest.parent.is_dir() or dest.parent.is_symlink():
            raise RuntimeError('DESTINATION_DIR_MISSING_OR_SYMLINK: '+str(dest.parent))
        if dest.parent.stat().st_uid!=os.getuid():
            raise RuntimeError('DESTINATION_DIR_NOT_OWNED: '+str(dest.parent))
    if systemctl('is-enabled',TIMER)!='enabled' or systemctl('is-active',TIMER)!='active':
        raise RuntimeError('EXISTING_STATUS_TIMER_NOT_ACTIVE')
    start=systemctl('show',SERVICE,'-p','ExecStart','--value')
    if 'meteor-status-agent.py' not in start:
        raise RuntimeError('EXISTING_AGENT_NOT_FOUND')
    post=systemctl('show',SERVICE,'-p','ExecStartPost','--value')
    if post.count('argv[]=')!=1 or post.count('meteor-heartbeat-watch.py')!=1:
        raise RuntimeError('EXISTING_POST_HOOKS_CHANGED')
    if systemctl('show',SERVICE,'-p','LoadState','--value')!='loaded':
        raise RuntimeError('STATUS_SERVICE_NOT_LOADED')
    parent=HOME/'.local/state/meteormesh'
    if not parent.is_dir() or parent.is_symlink():
        raise RuntimeError('OBSERVATION_STATE_DIR_MISSING')
    ps=parent.stat()
    if ps.st_uid!=os.getuid() or ps.st_mode&0o077:
        raise RuntimeError('OBSERVATION_STATE_DIRECTORY_INSECURE')
    return post

def pull(commit,name,blobsha):
    url=('https://raw.githubusercontent.com/comolokko45/meteormesh/'
         +commit+'/tools/'+name)
    with urlopen(url,timeout=18) as response:
        raw=response.read(100001)
    if len(raw)>100000:raise RuntimeError('SOURCE_OVERSIZE:'+name)
    b=b'blob '+str(len(raw)).encode()+b'\x00'+raw
    if hashlib.sha1(b).hexdigest()!=blobsha:
        raise RuntimeError('SOURCE_HASH_INVALID:'+name)
    compile(raw,name,'exec')
    return raw

def exclusive_create(dest,raw,mode):
    fd,tmp=tempfile.mkstemp(prefix='.ops-journal-stage-',dir=dest.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            os.fchmod(f.fileno(),mode)
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp,dest,follow_symlinks=False)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def install():
    before()
    downloaded={name:pull(c,p,s) for name,(c,p,s) in SRC.items()}
    unit=('[Service]\n'
          '# Retains existing heartbeat ExecStartPost; no reset line.\n'
          'ExecStartPost=-/usr/bin/python3 -B '+str(BASE/'meteor-ops-journal.py')+'\n')
    created=[]
    try:
        for name in SRC:
            dest=BASE/name
            exclusive_create(dest,downloaded[name],0o600)
            created.append(dest)
        exclusive_create(DROP,unit.encode(),0o644)
        created.append(DROP)
        systemctl('daemon-reload')
        effective=systemctl('show',SERVICE,'-p','ExecStartPost','--value')
        if (effective.count('argv[]=')!=2
            or effective.count('meteor-heartbeat-watch.py')!=1
            or effective.count('meteor-ops-journal.py')!=1):
            raise RuntimeError('EFFECTIVE_TWO_POST_HOOKS_NOT_VERIFIED')
        if systemctl('is-active',TIMER)!='active':
            raise RuntimeError('TIMER_CHANGED')
        print('METEORMESH_OPS_JOURNAL_INSTALL: PASS')
        print('EXISTING_HEARTBEAT_POST_HOOK: PRESERVED')
        print('ADDED_DAILY_DIGEST_AND_CHANGE_EVENT_HOOK: VERIFIED')
        print('TIMERS: UNCHANGED')
        print('LOCAL_STATE: USER_SSD_ONLY')
        print('EXTERNAL_PUSH: DISABLED')
        print('DATABASE_VIDEO_HDD: UNCHANGED')
        print('NEXT_TIMER_RUN: WILL_CREATE_LOCAL_DIGEST_STATE')
        print('ROLLBACK_DROP: '+str(DROP))
        print('ROLLBACK_SOURCES: '+', '.join(str(BASE/n) for n in SRC))
        print('ROLLBACK_RELOAD: systemctl --user daemon-reload')
    except BaseException:
        for dest in reversed(created):
            dest.unlink(missing_ok=True)
        try: systemctl('daemon-reload')
        except Exception:pass
        raise

if __name__=='__main__':
    try: install()
    except (RuntimeError,OSError,ValueError,subprocess.SubprocessError,
            TimeoutError) as e:
        print('METEORMESH_OPS_JOURNAL_INSTALL_STOPPED:',
              type(e).__name__,str(e)[:230],file=sys.stderr)
        sys.exit(2)
