#!/usr/bin/env python3
"""Reversible inactive RC1.3 Carryover morning.service config cutover.

Does NOT enable a timer, start a service, contact a phone or process files.
"""
from __future__ import annotations
import os
import re
import subprocess
import sys
from pathlib import Path

HOME=Path.home()
ROOT=HOME/'.local/share/meteormesh'
STAGE=ROOT/'staging/fullnight'
HUB=ROOT/'releases/rc13-20261030-carryover/MeteorHub'
CAND=STAGE/'meteormesh-morning-rc13-carryover.service'
OVERRIDE=HOME/'.config/systemd/user/meteormesh-morning.service.d/60-rc13-carryover.conf'
BACKUP=ROOT/'staging/rollback-rc12-20261030'
TIMER='meteormesh-morning.timer'
SERVICE='meteormesh-morning.service'

def run(*cmd,timeout=15):
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)

def need(ok,msg):
    if not ok: raise RuntimeError(msg)

def state(verb,unit):
    return run('systemctl','--user',verb,unit).stdout.strip()

def unit_lines(raw,prefix):
    return [s.strip() for s in raw.splitlines() if s.lstrip().startswith(prefix)]

def main():
    need(os.geteuid()!=0,'Do not run as root')
    need((state('is-enabled',TIMER),state('is-active',TIMER),state('is-active',SERVICE))==('disabled','inactive','inactive'),
         'Morning timer must remain disabled and morning service inactive')
    need(HUB.is_dir() and not HUB.is_symlink() and CAND.is_file() and not CAND.is_symlink(),
         'RC1.3 Carryover release/service candidate absent')
    need(all((BACKUP/name).is_file() and (BACKUP/name).stat().st_size>40
             for name in ('service-before.txt','timer-before.txt')),
         'Rollback snapshot missing')
    need((HUB/'night_scope.py').is_file() and (HUB/'meteor_morning.py').is_file(), 'Source code missing')
    p=STAGE/'night-scope-v1.json'
    need(p.is_file() and not p.is_symlink() and '"DISARMED"' in p.read_text(),
         'Original DISARMED baseline absent')
    raw=CAND.read_text()
    need('RequiresMountsFor=/srv/meteor-data' in raw and
         'ConditionPathIsMountPoint=/srv/meteor-data' in raw and
         '--max-segments-per-device 100' in raw and '--max-jobs 300' in raw and
         '--min-free-gb 100' in raw, 'Unexpected service budget or disk guards')
    need(str(HUB) in raw and str(STAGE/'night-scope-current.json') in raw,
         'Candidate paths are not the stable Carryover release/current policy')
    need(str(STAGE/'meteor-auto-night-policy.py') in raw,
         'Automatic nightly scope generator not wired in candidate')
    starts=unit_lines(raw,'ExecStart=')
    pre=unit_lines(raw,'ExecStartPre=')
    need(len(starts)==1 and len(pre)==2,'Expected one ExecStart and two ExecStartPre commands')
    need(starts[0].startswith('ExecStart=/usr/bin/python3 -B ') and
         '--night-scope ' in starts[0] and 'meteor_morning.py run ' in starts[0],
         'Unexpected production start command')
    need(any('mm01_readonly_preflight.py' in x for x in pre) and
         any('meteor-auto-night-policy.py --write' in x for x in pre),
         'Missing preflight/night scope prereq')
    need('METEORMESH_ISOLATED_TEST_ROOT' not in raw,'Unsafe test override found')
    # Avoid conflicting drop-ins that might supersede this override.
    if OVERRIDE.parent.is_dir():
        for item in OVERRIDE.parent.glob('*.conf'):
            if item==OVERRIDE: continue
            need(not item.is_symlink(),'Symlinked service drop-in')
            content=item.read_text()
            need(not re.search(r'^\s*ExecStart\s*=',content,re.M),
                 'A different ExecStart drop-in exists; manual review required')
    old=run('systemctl','--user','show',SERVICE,'-p','ExecStart','--value')
    need(old.returncode==0 and old.stdout.strip(),'Cannot read currently configured service')
    need(str(HUB) not in old.stdout or OVERRIDE.is_file(), 'Carryover already active without owned override')
    # Use an ExecStart-only user service override, preserve unrelated fields.
    new='[Unit]\nRequiresMountsFor=/srv/meteor-data\nConditionPathIsMountPoint=/srv/meteor-data\n\n[Service]\n'
    new+='WorkingDirectory='+str(HUB)+'\n'
    new+='ExecStart=\nExecStartPre=\n'
    new+='\n'.join(pre+[starts[0]])+'\n'
    new+='TimeoutStartSec=13h\nNice=10\nIOSchedulingClass=best-effort\nIOSchedulingPriority=7\n'
    need('ExecStart=\n' in new and new.count('ExecStartPre=')==3,'Generated override invalid')
    OVERRIDE.parent.mkdir(parents=True,exist_ok=True)
    if OVERRIDE.exists() or OVERRIDE.is_symlink():
        need(OVERRIDE.is_file() and not OVERRIDE.is_symlink() and OVERRIDE.read_text()==new,
             'Existing override differs: no changes')
        changed=False
    else:
        fd=os.open(OVERRIDE,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            with os.fdopen(fd,'w') as out:
                out.write(new); out.flush(); os.fsync(out.fileno())
            changed=True
        except Exception:
            OVERRIDE.unlink(missing_ok=True)
            raise
    try:
        need(run('systemctl','--user','daemon-reload').returncode==0,'systemctl daemon-reload failed')
        effective=run('systemctl','--user','show',SERVICE,'-p','ExecStart','--value')
        need(effective.returncode==0 and str(HUB) in effective.stdout
             and '--night-scope' in effective.stdout,'Active ExecStart does not refer to RC1.3 Carryover')
        need(state('is-enabled',TIMER)=='disabled' and state('is-active',TIMER)=='inactive'
             and state('is-active',SERVICE)=='inactive', 'Timer/service unexpected state after reload')
    except Exception:
        if changed and OVERRIDE.is_file() and OVERRIDE.read_text()==new:
            OVERRIDE.unlink()
            run('systemctl','--user','daemon-reload')
        raise
    print('RC13_CARRYOVER_SERVICE_CONFIG: PASS')
    print('SYSTEMD_EXECSTART: RC1.3 CARRYOVER')
    print('AUTO_NIGHT_POLICY: WIRED')
    print('MORNING_TIMER: DISABLED; SERVICE: INACTIVE')
    print('PRODUCTION_VIDEO_SQLITE: UNTOUCHED')
    print('ROLLBACK: rm '+str(OVERRIDE)+' && systemctl --user daemon-reload')
    print('NEXT: controlled manual acceptance; explicit authorization before timer enable')
    print('===== RC13 CONFIG CUTOVER COMPLETE =====')

if __name__=='__main__':
    try: main()
    except (RuntimeError,OSError,ValueError,subprocess.TimeoutExpired) as ex:
        print('STOP:',ex,file=sys.stderr)
        sys.exit(2)
