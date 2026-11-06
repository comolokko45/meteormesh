#!/usr/bin/env python3
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
