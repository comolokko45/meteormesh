#!/usr/bin/env python3
"""Create one safe, inactive RC1.3 systemd candidate from the existing RC1.2 draft.

No production changes, no timer activation, no disk/media/SQLite writes.
"""
from __future__ import annotations
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

home = Path.home()
base = home / '.local/share/meteormesh/staging/fullnight'
rc12 = home / '.local/share/meteormesh/releases/rc12-20261030/MeteorHub'
rc13 = base / 'night-rc13-8dzxti52/MeteorHub'
policy = base / 'night-scope-v1.json'
original = base / 'meteormesh-morning.service.candidate'
output = base / 'meteormesh-morning-rc13.service'
status_wrapper = home / '.local/lib/meteormesh/meteor-status-agent.py'

def systemctl(action, unit):
    p = subprocess.run(['systemctl','--user',action,unit], text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    return p.stdout.strip()

def deny(msg):
    raise RuntimeError('DENIED: ' + msg)

def main():
    if os.geteuid() == 0:
        deny('root forbidden')
    if systemctl('is-enabled','meteormesh-morning.timer') != 'disabled' or systemctl('is-active','meteormesh-morning.timer') != 'inactive':
        deny('morning timer must be disabled and inactive')
    if systemctl('is-active','meteormesh-morning.service') != 'inactive':
        deny('morning service must be inactive')
    effective = subprocess.run(['systemctl','--user','show','-p','ExecStart','--value','meteormesh-status.service'],capture_output=True,text=True,timeout=10)
    if effective.returncode or str(status_wrapper) not in effective.stdout:
        deny('MeteorAgent 1.1 is not linked to the existing status service')
    for path in (rc12/'meteor_morning.py',rc13/'meteor_morning.py',rc13/'night_scope.py',
                 rc13/'mm01_readonly_preflight.py',policy,original):
        if not path.is_file() or path.is_symlink():
            deny('missing or unsafe required source: '+str(path))
    if (policy.stat().st_mode & 0o077) != 0:
        deny('night scope permissions are not private')
    doc = json.loads(policy.read_text(encoding='utf8'))
    if (doc.get('schema') != 'meteormesh.night_scope.v1' or
        doc.get('status') != 'DISARMED' or doc.get('automatic_run_allowed') is not False or
        doc.get('ingest_scope_enforced') is not False or doc.get('queue_scope_enforced') is not False):
        deny('night scope must remain DISARMED')
    excluded = doc.get('excluded_segments',{})
    if set(excluded) != {'cam-viva','cam-ruby','cam-surya'} or sum(map(len, excluded.values())) != 14:
        deny('14 historical IDs not verified')
    raw = original.read_text(encoding='utf8')
    if raw.count(str(rc12)) < 3 or '--night-scope' in raw or 'ExecStart=/usr/bin/python3' not in raw:
        deny('unexpected RC1.2 service template')
    required = ['RequiresMountsFor=/srv/meteor-data','ConditionPathIsMountPoint=/srv/meteor-data',
                '--max-segments-per-device 100', '--max-jobs 300',
                '--max-runtime-hours 12', '--min-free-gb 100']
    if not all(r in raw for r in required):
        deny('required safety or bounded-work settings missing')
    updated = raw.replace(str(rc12),str(rc13)).replace(
        '--data-dir /srv/meteor-data', '--night-scope '+str(policy)+' --data-dir /srv/meteor-data')
    if (updated.count('--night-scope ') != 1 or
        'Environment=METEORMESH_ISOLATED_TEST_ROOT' in updated or
        str(rc12) in updated or
        updated.count(str(rc13)) < 3):
        deny('candidate consistency check failed')
    if output.is_symlink():
        deny('candidate target is symlink')
    if output.exists():
        if output.read_text(encoding='utf8') != updated:
            deny('different candidate exists; will not overwrite')
        print('RC13_CANDIDATE: EXISTING_IDENTICAL')
    else:
        fd = os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w',encoding='utf8') as file:
            file.write(updated)
        print('RC13_CANDIDATE: CREATED')
    # Verification checks syntax only; it does not start or enable units.
    p = subprocess.run(['systemd-analyze','verify',str(output)],capture_output=True,text=True,timeout=20)
    if p.returncode:
        print('SYSTEMD_VERIFY: WARNING', (p.stderr.strip() or p.stdout.strip())[:450])
    else:
        print('SYSTEMD_VERIFY: PASS')
    print('NIGHT_SCOPE: DISARMED; EXCLUDED_OLD_SEGMENTS: 14')
    print('AGENT: LINKED_TO_EXISTING_STATUS_TIMER')
    print('CANDIDATE:',output)
    print('MORNING_TIMER:',systemctl('is-enabled','meteormesh-morning.timer'))
    print('PRODUCTION_RELEASE: UNCHANGED; PHONE_VIDEO_SQLITE: UNTOUCHED')
    print('NEXT_GATE: Android security + fresh night policy + capacity acceptance')
    print('=== RC1.3 SERVICE STAGING READY ===')

if __name__=='__main__':
    try:
        main()
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as ex:
        print('STOP:',ex,file=sys.stderr)
        sys.exit(2)