#!/usr/bin/env python3
"""Install read-only post-analysis summary; does NOT alter capture, ingest, timers, data.

Fetches pinned companion script from GitHub and verifies SHA256 before installing.
"""
from __future__ import annotations
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen

SOURCE_URL='https://raw.githubusercontent.com/comolokko45/meteormesh/meteorhub-rc14-split/tools/mm14_night_snapshot.py'
SOURCE_SHA='1652e9e16af1c5a0fbc5197e53affea7127a2feb4532ed1bc408f0e5297e052d'
HOME=Path.home()
DEST=HOME/'.local/lib/meteormesh/meteor-night-snapshot.py'
DROP=HOME/'.config/systemd/user/meteormesh-analyze.service.d/20-night-snapshot.conf'

def run(*args):
    return subprocess.run(args,capture_output=True,text=True,timeout=12,check=False)

def stage(path: Path,content: bytes,mode:int):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name+'.')
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(content);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,mode)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def main():
    if os.geteuid()==0: raise RuntimeError('DO_NOT_RUN_AS_ROOT')
    if DEST.is_symlink() or DROP.is_symlink(): raise RuntimeError('SYMLINK_TARGET')
    active=run('systemctl','--user','show','meteormesh-analyze.service','-p','ExecStart','--value','--no-pager')
    if active.returncode or '--phase analyze' not in active.stdout or 'rc14-20261031-split' not in active.stdout:
        raise RuntimeError('RC14_ANALYZE_SERVICE_NOT_VERIFIED')
    raw=urlopen(SOURCE_URL,timeout=10).read(200000)
    if hashlib.sha256(raw).hexdigest()!=SOURCE_SHA:
        raise RuntimeError('SNAPSHOT_SCRIPT_HASH_MISMATCH')
    compile(raw,str(DEST),'exec')
    drop=('[Service]\n'
          'ExecStopPost=/usr/bin/python3 -B '+str(DEST)+'\n').encode()
    if DROP.exists() or DEST.exists():
        if not DROP.is_file() or not DEST.is_file() or DROP.read_bytes()!=drop or DEST.read_bytes()!=raw:
            raise RuntimeError('EXISTING_INSTALL_DIFFERS')
        print('NIGHT_SNAPSHOT: ALREADY_INSTALLED')
        return
    stage(DEST,raw,0o600)
    try:
        stage(DROP,drop,0o644)
        d=run('systemctl','--user','daemon-reload')
        if d.returncode: raise RuntimeError('SYSTEMD_DAEMON_RELOAD_FAILED')
        status=run('systemctl','--user','show','meteormesh-analyze.service','-p','ExecStopPost','--value','--no-pager')
        if status.returncode or str(DEST) not in status.stdout:
            raise RuntimeError('EXECSTOPPOST_NOT_ACTIVE')
    except Exception:
        DROP.unlink(missing_ok=True)
        DEST.unlink(missing_ok=True)
        run('systemctl','--user','daemon-reload')
        raise
    print('NIGHT_SNAPSHOT_INSTALL: PASS')
    print('AFTER_ANALYZE: JOURNAL_REPORT')
    print('PARTIAL_OFFLINE: EXPLICIT')
    print('TIMERS_VIDEO_SQLITE: UNCHANGED')
    print('ARCHIVE_AND_DELETE: DISABLED')
    print('ROLLBACK: rm',DROP,DEST,'&& systemctl --user daemon-reload')

if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,subprocess.TimeoutExpired) as e:
        print('DURDU:',e,file=sys.stderr)
        sys.exit(2)