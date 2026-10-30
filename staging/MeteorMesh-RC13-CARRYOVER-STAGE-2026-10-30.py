#!/usr/bin/env python3
"""Stage a bounded carryover queue fix on HP G62; never modify live service or data."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
import zipfile
from pathlib import Path

HOME = Path.home()
PARENT = HOME / '.local/share/meteormesh/releases'
STAGE = HOME / '.local/share/meteormesh/staging/fullnight'
OLD = PARENT / 'rc13-20261030-night-scope/MeteorHub'
NEW = PARENT / 'rc13-20261030-carryover/MeteorHub'
OLD_SERVICE = STAGE / 'meteormesh-morning-rc13-release.service'
NEW_SERVICE = STAGE / 'meteormesh-morning-rc13-carryover.service'
SNAP = HOME / '.local/share/meteormesh/staging/rollback-rc12-20261030'
BASELINE = STAGE / 'night-scope-v1.json'
PKG_URL = ('https://raw.githubusercontent.com/comolokko45/meteormesh/'
           'meteormesh-night-scope-review-20261030/staging/'
           'MeteorMesh-RC13-CARRYOVER-2026-10-30.zip.b64')
PKG_SHA256 = 'f113cf3dea45303a60c1544396de33dbc7acc004d2102654996d293ed0f757b2'
ORIGINAL = {
    'hub_events.py': 'a5a90704716d0d683576205c9a1680345f9e329919ed2a9696e44ab2582fa97f',
    'hub_workflow.py': '417690e6171cf4a9a8b6a6a027c05d49dc5089bcec9d121676a6fc48d3f98568',
    'meteor_morning.py': '797f70cfe5de8123c47ed0bf266e27a3648a9cdeb4bf0c2c1837877940e0549f',
    'meteormesh_hub.py': '425a73e05e7ef3b24393ab70c6f4dc5a8672c65d891db6254e7df053632d06c5',
    'night_scope.py': '31da2ef5b651d5b878589a835b5b7aa5b576b8be310a7180e532debc087ff44e',
    'test_night_scope.py': 'cfb1f678d33cf82db4fe6731b4033a4d0efba943647abf0a5b553914e9ba080b',
}


def stop_unless(ok, message):
    if not ok:
        raise RuntimeError('DENIED: ' + message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def proc(*args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def validate_source(folder, digests):
    stop_unless(folder.is_dir() and not folder.is_symlink(), 'Missing or symlink source folder')
    for obj in folder.rglob('*'):
        stop_unless(not obj.is_symlink(), 'Source tree contains symlink')
    for name, expected in digests.items():
        path = folder / name
        stop_unless(path.is_file() and not path.is_symlink()
                    and sha(path.read_bytes()) == expected,
                    'SHA mismatch: ' + name)


def main():
    stop_unless(os.geteuid() != 0, 'Never run as root')
    states = (
        proc('systemctl', '--user', 'is-enabled', 'meteormesh-morning.timer').stdout.strip(),
        proc('systemctl', '--user', 'is-active', 'meteormesh-morning.timer').stdout.strip(),
        proc('systemctl', '--user', 'is-active', 'meteormesh-morning.service').stdout.strip(),
    )
    stop_unless(states == ('disabled', 'inactive', 'inactive'), 'Morning automation must stay off')
    show = proc('systemctl', '--user', 'show', '-p', 'ExecStart', '--value', 'meteormesh-morning.service')
    stop_unless(show.returncode == 0 and 'rc13-20261030-carryover' not in show.stdout,
                'Candidate is already active')
    for name in ('service-before.txt', 'timer-before.txt'):
        path = SNAP / name
        stop_unless(path.is_file() and not path.is_symlink() and path.stat().st_size > 40,
                    'Rollback snapshot missing: ' + name)
    stop_unless(BASELINE.is_file() and not BASELINE.is_symlink(), 'Night baseline missing')
    pol = json.loads(BASELINE.read_text())
    old = pol.get('excluded_segments', {})
    stop_unless(pol.get('status') == 'DISARMED'
                and pol.get('automatic_run_allowed') is False
                and {k: len(v) for k, v in old.items()}
                == {'cam-viva': 10, 'cam-ruby': 2, 'cam-surya': 2},
                'Unexpected baseline exclusion')
    validate_source(OLD, ORIGINAL)
    stop_unless(OLD_SERVICE.is_file() and not OLD_SERVICE.is_symlink(),
                'Existing stable daily unit not found')

    with urllib.request.urlopen(PKG_URL, timeout=25) as response:
        encoded = response.read(100000)
    stop_unless(len(encoded) < 100000, 'Package unexpectedly large')
    raw = base64.b64decode(encoded.strip(), validate=True)
    stop_unless(sha(raw) == PKG_SHA256, 'Overlay package hash differs')
    with zipfile.ZipFile(io.BytesIO(raw)) as zipf:
        contents = set(zipf.namelist())
        names = {'night_scope.py', 'hub_workflow.py', 'meteor_morning.py', 'test_night_scope.py'}
        stop_unless(contents == names | {'MANIFEST.json'}, 'Unexpected archive contents')
        stop_unless(zipf.testzip() is None, 'Package CRC error')
        manifest = json.loads(zipf.read('MANIFEST.json'))
        stop_unless(manifest.get('schema') == 'meteormesh.rc13.carryover.v1'
                    and manifest.get('base_sha256') == ORIGINAL
                    and set(manifest.get('source_sha256', {})) == names,
                    'Unexpected manifest')
        replacements = {n: zipf.read(n) for n in names}
        for n, b in replacements.items():
            stop_unless(sha(b) == manifest['source_sha256'][n], 'Wrong file digest: '+n)

    if NEW.parent.exists():
        validate_source(NEW, manifest['source_sha256'])
        for name in set(ORIGINAL) - names:
            stop_unless(sha((NEW / name).read_bytes()) == ORIGINAL[name],
                        'Unexpected unchanged module: ' + name)
        print('RELEASE: EXISTING_CARRYOVER_INTEGRITY_OK')
    else:
        PARENT.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(dir=PARENT, prefix='.carryover-stage-'))
        try:
            shutil.copytree(OLD, temp / 'MeteorHub', symlinks=False)
            for n, b in replacements.items():
                (temp / 'MeteorHub' / n).write_bytes(b)
            validate_source(temp / 'MeteorHub', manifest['source_sha256'])
            for name in set(ORIGINAL) - names:
                stop_unless(sha((temp / 'MeteorHub' / name).read_bytes()) == ORIGINAL[name],
                            'Unchanged module differs')
            os.rename(temp, NEW.parent)
            print('RELEASE: STAGED_SEPARATELY')
        finally:
            if temp.exists():
                shutil.rmtree(temp)

    test = subprocess.run(['/usr/bin/python3', '-B', '-m', 'unittest', '-q', 'test_night_scope'],
                          cwd=NEW, capture_output=True, text=True, timeout=30,
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'})
    stop_unless(test.returncode == 0 and 'Ran 13 tests' in test.stdout+test.stderr,
                'Carryover tests failed: ' + (test.stdout+test.stderr)[-400:])

    text = OLD_SERVICE.read_text()
    stop_unless(text.count(str(OLD)) >= 2 and text.count('ExecStart=') == 1
                and 'ExecStartPre=' in text
                and '--night-scope ' in text,
                'Unexpected stable service template')
    candidate = text.replace(str(OLD), str(NEW))
    if NEW_SERVICE.exists() or NEW_SERVICE.is_symlink():
        stop_unless(NEW_SERVICE.is_file() and not NEW_SERVICE.is_symlink()
                    and NEW_SERVICE.read_text() == candidate,
                    'Candidate already exists with different content')
    else:
        fd = os.open(NEW_SERVICE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd,'w') as out:
            out.write(candidate)
            out.flush()
            os.fsync(out.fileno())
    verified = proc('systemd-analyze', 'verify', str(NEW_SERVICE))
    stop_unless(verified.returncode == 0,
                'systemd service candidate invalid: ' + verified.stderr[:250])
    print('ROLLBACK_SNAPSHOT: PRESENT')
    print('BASELINE: 14_OLD_RECORDINGS_EXCLUDED')
    print('CARRYOVER_TESTS: 13/13 PASS')
    print('CARRYOVER_QUEUE: PREVIOUS_NIGHTS_ALLOWED_IF_VERIFIED_AND_AFTER_BASELINE')
    print('DAYLIGHT_AND_BASELINE: EXCLUDED')
    print('SERVICE_CANDIDATE: READY')
    print('RELEASE:', NEW)
    print('CANDIDATE:', NEW_SERVICE)
    print('PRODUCTION: UNCHANGED; MORNING_TIMER: DISABLED; NO_VIDEO_DB_WRITES')
    print('=== RC13 CARRYOVER STAGING ACCEPTED ===')

if __name__=='__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, zipfile.BadZipFile,
            subprocess.TimeoutExpired, urllib.error.URLError) as exc:
        print('STOP:', exc, file=sys.stderr)
        sys.exit(2)