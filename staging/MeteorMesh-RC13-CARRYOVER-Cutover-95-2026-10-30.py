#!/usr/bin/env python3
"""Reversible RC1.3 Carryover systemd morning service *configuration* cutover.

This changes only a user systemd drop-in and daemon-reloads; it never starts
or enables any timer/service and never writes to the video/database directories.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
ROOT = HOME / '.local/share/meteormesh'
STAGE = ROOT / 'staging/fullnight'
HUB = ROOT / 'releases/rc13-20261030-carryover/MeteorHub'
CANDIDATE = STAGE / 'meteormesh-morning-rc13-carryover.service'
CONF_DIR = HOME / '.config/systemd/user/meteormesh-morning.service.d'
OLD = CONF_DIR / '90-meteormesh-rc12.conf'
NEW = CONF_DIR / '95-meteormesh-rc13-carryover.conf'
BACKUP = ROOT / 'staging/rollback-rc12-20261030'
SERVICE = 'meteormesh-morning.service'
TIMER = 'meteormesh-morning.timer'


def call(*argv: str, timeout: int = 15):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def require(ok, explanation):
    if not ok:
        raise RuntimeError('STOP: ' + explanation)


def state(op, unit):
    p = call('systemctl', '--user', op, unit)
    return p.stdout.strip()


def get(raw, key):
    return [line.strip() for line in raw.splitlines() if re.match(r'^\s*' + re.escape(key) + r'\s*=', line)]


def main():
    require(os.geteuid() != 0, 'Do not run as root')
    require((state('is-enabled', TIMER), state('is-active', TIMER), state('is-active', SERVICE))
            == ('disabled', 'inactive', 'inactive'),
            'Morning timer must remain disabled and service inactive')
    require(OLD.is_file() and not OLD.is_symlink(), 'Existing RC1.2 90-drop-in missing')
    old_text = OLD.read_text(encoding='utf8')
    old_starts = [x for x in get(old_text, 'ExecStart') if x.split('=', 1)[1].strip()]
    require(len(old_starts) == 1 and 'rc12-20261030/MeteorHub/meteor_morning.py run' in old_starts[0]
            and '--max-segments-per-device 1' in old_starts[0]
            and '--max-jobs 1' in old_starts[0] and '--min-free-gb 20' in old_starts[0],
            'Existing RC1.2 unit is not the known baseline')
    for name in ('service-before.txt', 'timer-before.txt'):
        p = BACKUP / name
        require(p.is_file() and not p.is_symlink() and p.stat().st_size > 40,
                'Rollback snapshot missing: ' + name)
    require(CANDIDATE.is_file() and not CANDIDATE.is_symlink(), 'Carryover candidate absent')
    for filename in ('meteor_morning.py', 'night_scope.py', 'hub_workflow.py',
                     'mm01_readonly_preflight.py'):
        p = HUB / filename
        require(p.is_file() and not p.is_symlink(), 'Carryover release incomplete: ' + filename)
    raw = CANDIDATE.read_text(encoding='utf8')
    required = ('RequiresMountsFor=/srv/meteor-data',
                'ConditionPathIsMountPoint=/srv/meteor-data',
                '--max-segments-per-device 100', '--max-jobs 300',
                '--max-runtime-hours 12', '--min-free-gb 100')
    require(all(x in raw for x in required), 'Candidate disk gates or bounded work options missing')
    require(str(HUB) in raw and str(STAGE / 'night-scope-current.json') in raw,
            'Candidate must use persistent Carryover release and daily night scope')
    require(str(STAGE / 'meteor-auto-night-policy.py') in raw,
            'Candidate missing the daily night generator')
    require('METEORMESH_ISOLATED_TEST_ROOT' not in raw,
            'Candidate contains development-only root override')
    starts, prestarts = get(raw, 'ExecStart'), get(raw, 'ExecStartPre')
    require(len(starts) == 1 and len(prestarts) == 2, 'Expected 1 ExecStart + 2 ExecStartPre')
    require(starts[0].startswith('ExecStart=/usr/bin/python3 -B ')
            and 'meteor_morning.py run' in starts[0] and '--night-scope ' in starts[0],
            'Unexpected Carryover start command')
    require(any('mm01_readonly_preflight.py' in x for x in prestarts)
            and any('meteor-auto-night-policy.py --write' in x for x in prestarts),
            'Expected mount preflight and daily scope generator')
    for other in CONF_DIR.glob('*.conf'):
        require(not other.is_symlink(), 'Symlinked service drop-in: ' + str(other))
        if other in (OLD, NEW):
            continue
        # Only the known RC1.2 ExecStart is allowed alongside our new override.
        require(not any(x.split('=', 1)[1].strip() for x in get(other.read_text(), 'ExecStart')),
                'Unknown ExecStart drop-in: ' + str(other))
    # Higher lexicographic priority than 90-rc12, preserving original for rollback.
    rendered = '[Unit]\nRequiresMountsFor=/srv/meteor-data\nConditionPathIsMountPoint=/srv/meteor-data\n'
    rendered += '\n[Service]\nWorkingDirectory=' + str(HUB) + '\n'
    rendered += 'ExecStart=\nExecStartPre=\n' + '\n'.join(prestarts) + '\n'
    rendered += starts[0] + '\nTimeoutStartSec=13h\nNice=10\n'
    rendered += 'IOSchedulingClass=best-effort\nIOSchedulingPriority=7\n'
    require(rendered.count('ExecStart=') == 2 and rendered.count('ExecStartPre=') == 3,
            'Internal generated service override invalid')
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    fresh = False
    if NEW.exists() or NEW.is_symlink():
        require(NEW.is_file() and not NEW.is_symlink() and NEW.read_text() == rendered,
                'Existing 95-drop-in differs; no overwrite')
    else:
        fd = os.open(NEW, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w', encoding='utf8') as f:
            f.write(rendered)
            f.flush()
            os.fsync(f.fileno())
        fresh = True
    try:
        require(call('systemctl', '--user', 'daemon-reload').returncode == 0,
                'daemon-reload failed')
        p = call('systemctl', '--user', 'show', '-p', 'ExecStart', '--value', SERVICE)
        require(p.returncode == 0 and str(HUB) in p.stdout and '--night-scope ' in p.stdout,
                'Effective ExecStart is not RC1.3 Carryover')
        q = call('systemctl', '--user', 'show', '-p', 'ExecStartPre', '--value', SERVICE)
        require(q.returncode == 0 and 'meteor-auto-night-policy.py' in q.stdout
                and 'mm01_readonly_preflight.py' in q.stdout,
                'Effective ExecStartPre gates are not both present')
        require((state('is-enabled', TIMER), state('is-active', TIMER), state('is-active', SERVICE))
                == ('disabled', 'inactive', 'inactive'),
                'Unexpected systemd state after cutover')
        require(OLD.is_file() and OLD.read_text() == old_text, 'RC1.2 rollback file changed')
    except Exception:
        if fresh and NEW.is_file() and NEW.read_text() == rendered:
            NEW.unlink()
            call('systemctl', '--user', 'daemon-reload')
        raise
    print('RC13_CARRYOVER_95_CUTOVER: PASS')
    print('RC12_90_DROPIN: PRESERVED')
    print('CARRYOVER_EXECSTART: VERIFIED')
    print('DAILY_SCOPE_AND_PREFLIGHT: VERIFIED')
    print('MORNING_TIMER: DISABLED; SERVICE: INACTIVE')
    print('VIDEO_AND_DATABASE: UNTOUCHED')
    print('ROLLBACK: rm ' + str(NEW) + ' && systemctl --user daemon-reload')
    print('=== RC13 CARRYOVER CONFIG CUTOVER COMPLETE ===')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
