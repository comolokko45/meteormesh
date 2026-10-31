#!/usr/bin/env python3
"""MeteorMesh: read-only remote node health checks on existing 30-minute status service.

No camera control, network changes, SQLite/HDD access or timer modifications.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

HOME = Path.home()
WATCH = HOME / '.local/lib/meteormesh/meteor-phone-watch.py'
DROP = HOME / '.config/systemd/user/meteormesh-status.service.d/40-meteormesh-phone-watch.conf'
SERVICE = 'meteormesh-status.service'
TIMER = 'meteormesh-status.timer'

WATCH_CODE = r'''#!/usr/bin/env python3
"""Observe MeteorNode LAN health. Consecutive failures => journal alarm, no side effects.

State file lives in user home, not on the MeteorMesh data disk.
"""
import datetime
import json
import os
from pathlib import Path
import tempfile
import urllib.request

PHONES = {
    'viva': '192.168.0.216',
    'ruby': '192.168.0.237',
    'surya': '192.168.0.105',
}
STATE = Path.home() / '.local/state/meteormesh/phone-watch.json'


def probe(ip):
    url = 'http://' + ip + ':8080/api/v1/health'
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            if response.status != 200:
                return False, 'HTTP_' + str(response.status)
            obj = json.load(response)
        if not isinstance(obj, dict) or not str(obj.get('app_version', '')).startswith('0.'):
            return False, 'UNEXPECTED_HEALTH'
        return True, str(obj.get('state', 'unknown'))[:70]
    except Exception as exc:
        return False, type(exc).__name__


def main():
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    try:
        previous = json.loads(STATE.read_text())
        if not isinstance(previous, dict): previous = {}
    except (OSError, ValueError):
        previous = {}
    updated = {}
    for name, ip in PHONES.items():
        ok, detail = probe(ip)
        prior = previous.get(name, {})
        if not isinstance(prior, dict): prior = {}
        failed = 0 if ok else min(999, max(0, int(prior.get('failures', 0))) + 1)
        flagged = not ok and failed >= 2
        was_flagged = prior.get('alerted') is True
        last_good = now if ok else prior.get('last_online_utc')
        updated[name] = {'failures': failed, 'alerted': flagged,
                         'last_online_utc': last_good, 'ip': ip}
        if flagged and not was_flagged:
            print(f'PHONE_OFFLINE: {name} {ip}:8080 consecutive_failures={failed} reason={detail}', flush=True)
        elif ok and was_flagged:
            print(f'PHONE_RECOVERED: {name} {ip}:8080 state={detail}', flush=True)
        else:
            tag = 'ONLINE' if ok else ('OFFLINE_KNOWN' if flagged else 'UNREACHABLE_FIRST_CHECK')
            print(f'PHONE_{tag}: {name} {ip}:8080 detail={detail}', flush=True)
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, path = tempfile.mkstemp(dir=STATE.parent, prefix='.phone-watch-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(updated, f, sort_keys=True)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.chmod(path, 0o600)
        os.replace(path, STATE)
    finally:
        if os.path.exists(path): os.unlink(path)
    print('PHONE_WATCH: 3 devices checked; no camera/recording changes', flush=True)

if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print('PHONE_WATCH_ERROR: ' + type(exc).__name__, flush=True)
        # Observability must not fail the working status service.
'''


def cmd(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=15, check=True).stdout.strip()


def write_once(p: Path, data: str, mode: int):
    if p.is_symlink():
        raise RuntimeError('SYMLINK_BLOCKED: ' + str(p))
    if p.exists():
        if p.read_text() != data:
            raise RuntimeError('EXISTING_FILE_DIFFERENT: ' + str(p))
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + p.name, dir=p.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return True


def main():
    if os.geteuid() == 0:
        raise RuntimeError('DO_NOT_RUN_AS_ROOT')
    if cmd('systemctl', '--user', 'is-enabled', TIMER) != 'enabled':
        raise RuntimeError('STATUS_TIMER_NOT_ENABLED')
    status_cmd = cmd('systemctl', '--user', 'show', SERVICE, '-p', 'ExecStart', '--value')
    if 'meteor-status-agent.py' not in status_cmd:
        raise RuntimeError('EXISTING_STATUS_AGENT_NOT_FOUND')
    if 'meteormesh-status' not in str(DROP) or not str(WATCH).startswith(str(HOME)):
        raise RuntimeError('INVALID_PATH')
    compile(WATCH_CODE, str(WATCH), 'exec')
    config = '[Service]\nExecStartPost=/usr/bin/python3 -B ' + str(WATCH) + '\n'
    created_watch = write_once(WATCH, WATCH_CODE, 0o600)
    created_drop = False
    try:
        created_drop = write_once(DROP, config, 0o644)
        cmd('systemctl', '--user', 'daemon-reload')
        effective = cmd('systemctl', '--user', 'show', SERVICE, '-p', 'ExecStartPost', '--value')
        if str(WATCH) not in effective:
            raise RuntimeError('EFFECTIVE_EXECSTARTPOST_MISMATCH')
    except Exception:
        if created_drop: DROP.unlink(missing_ok=True)
        if created_watch: WATCH.unlink(missing_ok=True)
        subprocess.run(['systemctl', '--user', 'daemon-reload'], capture_output=True)
        raise
    print('PHONE_WATCH_INSTALL: PASS')
    print('STATUS_TIMER: UNCHANGED_30MIN')
    print('OFFLINE_ALARM: AFTER_2_CONSECUTIVE_FAILURES')
    print('ONLINE_RECOVERY: AUTOMATIC')
    print('ALERT_CHANNEL: SYSTEMD_JOURNAL_ONLY')
    print('CAMERA_RECORDING_DATABASE: UNTOUCHED')
    print('ROLLBACK: rm ' + str(DROP) + ' && systemctl --user daemon-reload')
    subprocess.run(['/usr/bin/python3', '-B', str(WATCH)], check=False, timeout=20)

if __name__ == '__main__':
    try: main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print('DURDU:', exc, file=sys.stderr)
        sys.exit(2)
