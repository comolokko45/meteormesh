#!/usr/bin/env python3
"""Install one heartbeat observer hook onto EXISTING MeteorMesh status service.

Never changes old drop-ins, timers, DB, HDD, videos, network, phone controls.
Fails closed unless the currently effective ExecStartPost is the legacy
phone watcher and no other hooks. Creates two new SSD files only.
Rollback automatically on failure and print reversible manual uninstall.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import urllib.request

HOME = Path.home()
SRC_COMMIT = '4a37ed523aaccfa38e31f0f65824f9248af72c76'
SRC_PATH = 'tools/mm_ops_heartbeat_watch_20261101.py'
EXPECTED_BLOB = 'f8fe2d369aceb9af661f3bfee5f415428518e9eb'
SRC_URL = ('https://raw.githubusercontent.com/comolokko45/meteormesh/'
           + SRC_COMMIT + '/' + SRC_PATH)
DEST = HOME/'.local/lib/meteormesh/meteor-heartbeat-watch.py'
DROP = HOME/'.config/systemd/user/meteormesh-status.service.d/99-heartbeat-watch.conf'
SERVICE='meteormesh-status.service'
TIMER='meteormesh-status.timer'
OLD_WATCH='meteor-phone-watch.py'

def systemctl(*args):
    p = subprocess.run(['systemctl','--user',*args],capture_output=True,
                       text=True,timeout=15)
    if p.returncode:
        raise RuntimeError('SYSTEMD_'+args[0].upper()+'_ERROR: '+
                           (p.stderr or p.stdout).strip()[:120])
    return p.stdout.strip()

def check_preconditions():
    if os.geteuid()==0:
        raise RuntimeError('DO_NOT_RUN_AS_ROOT')
    for target in (DEST,DROP):
        if target.exists() or target.is_symlink():
            raise RuntimeError('ALREADY_EXISTS_NO_OVERWRITE: '+str(target))
        if not target.parent.is_dir() or target.parent.is_symlink():
            raise RuntimeError('TARGET_PARENT_INVALID: '+str(target.parent))
        st=target.parent.stat()
        if st.st_uid!=os.getuid():
            raise RuntimeError('TARGET_PARENT_NOT_OWNED: '+str(target.parent))
    if systemctl('is-active',TIMER)!='active' or systemctl('is-enabled',TIMER)!='enabled':
        raise RuntimeError('EXISTING_STATUS_TIMER_NOT_ACTIVE')
    if systemctl('is-active','meteormesh-control-013.service')!='active':
        raise RuntimeError('CONTROL_8443_SERVICE_NOT_ACTIVE')
    main=systemctl('show',SERVICE,'-p','ExecStart','--value')
    if 'meteor-status-agent.py' not in main:
        raise RuntimeError('STATUS_AGENT_EXECSTART_NOT_FOUND')
    post=systemctl('show',SERVICE,'-p','ExecStartPost','--value')
    # Protect any other existing observer hooks from accidental override.
    if post.count('argv[]=')!=1 or post.count(OLD_WATCH)!=1:
        raise RuntimeError('UNEXPECTED_EXISTING_POST_HOOK: REVIEW_MANUALLY')
    success=systemctl('show',SERVICE,'-p','SuccessExitStatus','--value')
    if '1' not in success.split():
        raise RuntimeError('EXPECTED_WARNING_EXIT_POLICY_MISSING')
    return post

def fetch_source():
    with urllib.request.urlopen(SRC_URL,timeout=15) as rsp:
        raw=rsp.read(100001)
    if len(raw)>100000:raise RuntimeError('WATCH_SOURCE_OVERSIZE')
    blob=b'blob '+str(len(raw)).encode()+b'\0'+raw
    if hashlib.sha1(blob).hexdigest()!=EXPECTED_BLOB:
        raise RuntimeError('WATCH_SOURCE_HASH_MISMATCH')
    compile(raw,SRC_PATH,'exec')
    return raw

def run_src(path,flag):
    p=subprocess.run([sys.executable,'-B',str(path),flag],capture_output=True,
                     text=True,timeout=15)
    if p.returncode:
        raise RuntimeError('WATCH_'+flag.upper()+'_FAILED: '+
                           (p.stderr or p.stdout)[:150])
    if flag=='--self-test' and 'HEARTBEAT_WATCH_SELFTEST: PASS' not in p.stdout:
        raise RuntimeError('SELFTEST_MARKER_NOT_FOUND')
    if flag=='--dry-run':
        if not all(t in p.stdout for t in ('"mode": "READ_ONLY_DRY_RUN"',
                                         '"phones_contacted": false',
                                         '"production_db_opened": false')):
            raise RuntimeError('DRY_RUN_NOT_READ_ONLY')
    return p.stdout

def install_new_file(path,raw,mode):
    # Hard-link onto a new target: unlike os.replace, never overwrites old files.
    fd,tmp=tempfile.mkstemp(prefix='.heartbeat-stage-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            os.fchmod(f.fileno(),mode)
            f.write(raw);f.flush();os.fsync(f.fileno())
        os.link(tmp,path,follow_symlinks=False)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def main():
    check_preconditions()
    raw=fetch_source()
    # Verify behavior on a temporary file before modifying systemd.
    with tempfile.TemporaryDirectory(prefix='.heartbeat-audit-',dir=DEST.parent) as d:
        t=Path(d)/'watch.py'
        t.write_bytes(raw)
        print(run_src(t,'--self-test').strip(),flush=True)
        preview=run_src(t,'--dry-run')
        if '"source": "LOCAL_TLS_8443_HEARTBEATS"' not in preview:
            raise RuntimeError('LOCAL_CONTROL_SOURCE_MISMATCH')
        print('LOCAL_HTTPS_READ_ONLY_DRY_RUN: PASS',flush=True)
    unit=('[Service]\n'
          '# Replaces ONE legacy static-IP ExecStartPost; leaves timer & Agent intact.\n'
          'ExecStartPost=\n'
          'ExecStartPost=-/usr/bin/python3 -B '+str(DEST)+' --commit\n')
    created=[]
    try:
        install_new_file(DEST,raw,0o600);created.append(DEST)
        install_new_file(DROP,unit.encode(),0o644);created.append(DROP)
        systemctl('daemon-reload')
        active=systemctl('show',SERVICE,'-p','ExecStartPost','--value')
        if ('meteor-heartbeat-watch.py' not in active
            or 'meteor-phone-watch.py' in active
            or active.count('argv[]=')!=1):
            raise RuntimeError('EFFECTIVE_POST_HOOK_MISMATCH')
        if systemctl('is-active',TIMER)!='active':
            raise RuntimeError('TIMER_STATE_CHANGED')
        print('HEARTBEAT_STATUS_INTEGRATION: PASS')
        print('OLD_WATCH: PRESERVED_BUT_NOT_EXECUTED')
        print('EXISTING_AGENT_TIMER: UNCHANGED')
        print('NEXT_TIMER: OBSERVATION_STATE_ONLY; NO_PUSH_DELIVERY')
        print('CAMERA_MP4_SQLITE_HDD: UNCHANGED')
        print('ROLLBACK_REMOVE: '+str(DROP))
        print('ROLLBACK_REMOVE: '+str(DEST))
        print('ROLLBACK_THEN: systemctl --user daemon-reload')
    except BaseException:
        for p in reversed(created):
            p.unlink(missing_ok=True) # ONLY files just created by this script.
        try:systemctl('daemon-reload')
        except Exception:pass
        raise

if __name__=='__main__':
    try: main()
    except (RuntimeError,OSError,ValueError,subprocess.SubprocessError,
            urllib.error.URLError) as exc:
        print('HEARTBEAT_STATUS_INTEGRATION_STOPPED:',
              type(exc).__name__,str(exc)[:260],file=sys.stderr)
        sys.exit(2)
