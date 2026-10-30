#!/usr/bin/env python3
"""Stage RC1.3 as a stable, inactive release; no production unit, data or phone changes."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOME = Path.home()
STAGE = HOME / '.local/share/meteormesh/staging/fullnight'
SRC = STAGE / 'night-rc13-8dzxti52/MeteorHub'
SRC_UNIT = STAGE / 'meteormesh-morning-rc13-daily.service'
GENERATOR = STAGE / 'meteor-auto-night-policy.py'
BASELINE = STAGE / 'night-scope-v1.json'
RELEASE_PARENT = HOME / '.local/share/meteormesh/releases'
RELEASE = RELEASE_PARENT / 'rc13-20261030-night-scope'
HUB = RELEASE / 'MeteorHub'
UNIT = STAGE / 'meteormesh-morning-rc13-release.service'
SHA = {
    'hub_events.py':'a5a90704716d0d683576205c9a1680345f9e329919ed2a9696e44ab2582fa97f',
    'hub_workflow.py':'417690e6171cf4a9a8b6a6a027c05d49dc5089bcec9d121676a6fc48d3f98568',
    'meteor_morning.py':'797f70cfe5de8123c47ed0bf266e27a3648a9cdeb4bf0c2c1837877940e0549f',
    'meteormesh_hub.py':'425a73e05e7ef3b24393ab70c6f4dc5a8672c65d891db6254e7df053632d06c5',
    'night_scope.py':'31da2ef5b651d5b878589a835b5b7aa5b576b8be310a7180e532debc087ff44e',
    'test_night_scope.py':'cfb1f678d33cf82db4fe6731b4033a4d0efba943647abf0a5b553914e9ba080b',
}


def command(*args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def require(cond, message):
    if not cond:
        raise RuntimeError(message)


def verify_files(folder):
    require(folder.is_dir() and not folder.is_symlink(), 'Unsafe or absent release source')
    for path in folder.rglob('*'):
        require(not path.is_symlink(), 'Release contains a symlink: ' + str(path))
    for name, expected in SHA.items():
        p = folder / name
        require(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == expected,
                'Source integrity failed: ' + name)


def verify_baseline():
    require(BASELINE.is_file() and not BASELINE.is_symlink(), 'Missing baseline policy')
    require(BASELINE.stat().st_mode & 0o077 == 0, 'Baseline must be private')
    data = json.loads(BASELINE.read_text())
    require(data.get('status') == 'DISARMED' and data.get('automatic_run_allowed') is False,
            'Unexpected baseline state')
    excluded = data.get('excluded_segments', {})
    require({k:len(v) for k,v in excluded.items()} == {'cam-viva':10,'cam-ruby':2,'cam-surya':2},
            'Expected 14 excluded baseline segments')


def install_staging():
    require(os.geteuid() != 0, 'Do not run as root')
    states = tuple(command('systemctl','--user',act,'meteormesh-morning.'+unit).stdout.strip()
                   for act,unit in (('is-enabled','timer'),('is-active','timer'),('is-active','service')))
    require(states == ('disabled','inactive','inactive'), 'Morning timer/service must remain inactive')
    require(SRC.is_dir() and SRC_UNIT.is_file() and GENERATOR.is_file(), 'Missing staged RC1.3 source/unit')
    verify_files(SRC)
    verify_baseline()
    text = SRC_UNIT.read_text()
    srcstr = str(SRC)
    require(text.count(srcstr) >= 2 and text.count('ExecStart=') == 1,
            'Unexpected RC1.3 daily candidate')
    require(str(GENERATOR) in text and '--night-scope ' + str(STAGE/'night-scope-current.json') in text,
            'Daily policy is not hooked into service')
    require('RequiresMountsFor=/srv/meteor-data' in text and
            'ConditionPathIsMountPoint=/srv/meteor-data' in text and
            '--min-free-gb 100' in text, 'Missing mount/space gate')
    require('METEORMESH_ISOLATED_TEST_ROOT' not in text, 'Test-only root override in release service')
    active = command('systemctl','--user','show','meteormesh-morning.service','-p','ExecStart','--value').stdout
    require('rc13-20261030-night-scope' not in active,
            'RC1.3 already active; do not stage over a production deployment')
    RELEASE_PARENT.mkdir(parents=True, exist_ok=True)
    if RELEASE.exists():
        verify_files(HUB)
        print('RELEASE: EXISTING_INTEGRITY_OK')
    else:
        temp = Path(tempfile.mkdtemp(prefix='.rc13-stage-', dir=RELEASE_PARENT))
        try:
            shutil.copytree(SRC, temp / 'MeteorHub', symlinks=False)
            verify_files(temp / 'MeteorHub')
            os.rename(temp, RELEASE)
        finally:
            if temp.exists():
                shutil.rmtree(temp)
        print('RELEASE: STAGED_SEPARATELY')
    tests = subprocess.run(['/usr/bin/python3','-B','-m','unittest','-q','test_night_scope'],
                           cwd=HUB, capture_output=True, text=True, timeout=30)
    require(tests.returncode == 0 and 'Ran 10 tests' in tests.stderr + tests.stdout,
            'RC1.3 release scope tests failed: ' + (tests.stderr + tests.stdout)[-280:])
    next_unit = text.replace(srcstr, str(HUB))
    require(next_unit.count(str(HUB)) >= 2 and srcstr not in next_unit,
            'Stable source was not substituted')
    if UNIT.exists():
        require(not UNIT.is_symlink() and UNIT.read_text() == next_unit,
                'Existing release candidate differs: no overwrite')
    else:
        fd = os.open(UNIT, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd,'w') as out:
            out.write(next_unit)
    check = command('systemd-analyze','verify',str(UNIT),timeout=20)
    require(check.returncode == 0, 'systemd candidate syntax failed: ' + check.stderr[:320])
    print('RC13_RELEASE_STAGING: PASS')
    print('SOURCE_INTEGRITY: 6/6 MATCH')
    print('RELEASE_TESTS: 10/10 PASS')
    print('SYSTEMD_CANDIDATE: PASS')
    print('BASELINE_EXCLUDED: 14')
    print('CANDIDATE:', UNIT)
    print('RELEASE:', HUB)
    print('MORNING_TIMER: disabled; SERVICE: inactive')
    print('PRODUCTION_CHANGES: NONE; ANDROID_UNCHANGED')
    print('NEXT: review rollback and request explicit approval before changing live unit or timer')

if __name__ == '__main__':
    try:
        install_staging()
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print('STOP:',exc,file=sys.stderr)
        sys.exit(2)