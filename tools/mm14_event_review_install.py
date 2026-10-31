#!/usr/bin/env python3
"""Install append-only MeteorMesh independent camera event review indexes.

Never starts cameras or a video analysis run, never changes timers or deletes data.
Companion is pinned with SHA256; verifies approved RC1.4 analysis service first.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.request import urlopen

HOME=Path.home()
TARGET=HOME/'.local/lib/meteormesh/meteor-event-review.py'
DROP=HOME/'.config/systemd/user/meteormesh-analyze.service.d/30-event-review.conf'
SRC='https://raw.githubusercontent.com/comolokko45/meteormesh/meteorhub-rc14-split/tools/mm14_event_review.py?fix=20261031-release-a'
SOURCE_SHA='b06fef4baeb3ddb09fd052ff602036988a5784fcbc9209ba1381e12eca3147f7'


def command(*args,timeout=15):
    return subprocess.run(args,capture_output=True,text=True,timeout=timeout,check=False)


def install_new(path:Path,raw:bytes,mode:int):
    if path.exists() or path.is_symlink():raise RuntimeError('ALREADY_EXISTS: '+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name+'.')
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(raw);f.flush();os.fsync(f.fileno())
        os.chmod(temp,mode)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def main():
    if os.geteuid()==0:raise RuntimeError('DO_NOT_RUN_AS_ROOT')
    if TARGET.is_symlink() or DROP.is_symlink():raise RuntimeError('SYMLINK_DESTINATION')
    active=command('systemctl','--user','is-active','meteormesh-analyze.service')
    if active.stdout.strip()=='active':raise RuntimeError('ANALYSIS_CURRENTLY_RUNNING')
    start=command('systemctl','--user','show','meteormesh-analyze.service','-p','ExecStart','--value','--no-pager')
    if start.returncode or '--phase analyze' not in start.stdout or 'rc14-20261031-split' not in start.stdout:
        raise RuntimeError('RC14_ANALYSIS_BASELINE_NOT_VERIFIED')
    existing=HOME/'.config/systemd/user/meteormesh-analyze.service.d/20-night-snapshot.conf'
    if not existing.is_file() or existing.is_symlink():raise RuntimeError('NIGHT_SNAPSHOT_MISSING')
    raw=urlopen(SRC,timeout=15).read(100_000)
    if hashlib.sha256(raw).hexdigest()!=SOURCE_SHA:
        raise RuntimeError('COMPANION_HASH_MISMATCH')
    compile(raw,str(TARGET),'exec')
    drop=('[Service]\nExecStopPost=/usr/bin/python3 -B '+str(TARGET)+'\n').encode()
    if TARGET.exists() or DROP.exists():
        if (TARGET.is_file() and DROP.is_file() and TARGET.read_bytes()==raw
                and DROP.read_bytes()==drop):
            print('EVENT_REVIEW_INSTALL: ALREADY_INSTALLED')
            return
        raise RuntimeError('EXISTING_INSTALL_DIFFERS')
    install_new(TARGET,raw,0o600)
    try:
        install_new(DROP,drop,0o644)
        if command('systemctl','--user','daemon-reload').returncode:
            raise RuntimeError('DAEMON_RELOAD_FAILED')
        check=command('systemctl','--user','show','meteormesh-analyze.service',
                      '-p','ExecStopPost','--value','--no-pager')
        if check.returncode or str(TARGET) not in check.stdout or 'meteor-night-snapshot.py' not in check.stdout:
            raise RuntimeError('EVENT_OR_NIGHT_SNAPSHOT_HOOK_NOT_ACTIVE')
    except BaseException:
        DROP.unlink(missing_ok=True)
        TARGET.unlink(missing_ok=True)
        command('systemctl','--user','daemon-reload')
        raise
    print('EVENT_REVIEW_INSTALL: PASS')
    print('EACH_CAMERA: INDEPENDENT')
    print('CANDIDATE_INDEX: APPEND_ONLY_SHA_VERIFIED')
    print('60S_CONTEXT: METADATA_ONLY_NO_CLIP_YET')
    print('EXISTING_ANALYSIS_AND_TIMERS: UNCHANGED')
    print('AUTO_DELETE_AND_CLOUD_UPLOAD: OFF')
    print('ROLLBACK: rm',DROP,TARGET,'&& systemctl --user daemon-reload')

if __name__=='__main__':
    try: main()
    except (OSError,RuntimeError,ValueError,subprocess.TimeoutExpired) as exc:
        print('DURDU:',exc,file=sys.stderr)
        sys.exit(2)
