#!/usr/bin/env python3
"""MeteorMesh: one supervised 35-second LAN capture test on 3 Android nodes.

Starts and stops only cameras that were idle at preflight. Does not activate
systemd, download videos, delete media or change device configuration.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
CONF = HOME / '.config/meteormesh/devices-three-lab-rc12.json'
TOKENS = HOME / '.config/meteormesh/tokens.env'
EXPECTED = {'cam-viva': 'viva', 'cam-ruby': 'ruby', 'cam-surya': 'surya'}
DURATION = 35


def abort(reason):
    raise RuntimeError(reason)


def call(host, port, token, method, uri, timeout=6):
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        headers = {'Authorization': 'Bearer ' + token}
        if method == 'POST':
            headers['Content-Length'] = '0'
        conn.request(method, uri, body=b'' if method == 'POST' else None, headers=headers)
        res = conn.getresponse()
        payload = res.read(1024 * 1024)
        try:
            data = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            data = {'error': payload[:160].decode('utf-8', 'replace')}
        if res.status != (202 if method == 'POST' else 200):
            abort(f'{host} {uri}: HTTP {res.status} ({data.get("error", "unknown")})')
        if not isinstance(data, dict):
            abort(f'{host} {uri}: invalid JSON shape')
        return data
    finally:
        conn.close()


def get(d, uri):
    return call(d['host'], d['port'], d['token'], 'GET', uri)


def post(d, uri):
    return call(d['host'], d['port'], d['token'], 'POST', uri)


def segments(d):
    data = get(d, '/api/v1/segments')
    rows = data.get('segments')
    if not isinstance(rows, list):
        abort(f'{d["id"]}: segments shape invalid')
    return rows


def segment_keys(rows):
    result = set()
    for row in rows:
        if not isinstance(row, dict):
            abort('Invalid segment entry')
        value = next((row[key] for key in ('segment_id', 'id', 'filename', 'name') if isinstance(row.get(key), str) and row[key]), None)
        result.add(value if value is not None else json.dumps(row, sort_keys=True, ensure_ascii=True))
    return result


def service_preflight():
    for unit, action, expect in (
        ('meteormesh-morning.timer', 'is-enabled', 'disabled'),
        ('meteormesh-morning.timer', 'is-active', 'inactive'),
        ('meteormesh-morning.service', 'is-active', 'inactive'),
    ):
        p = subprocess.run(['systemctl', '--user', action, unit], capture_output=True, text=True, timeout=10)
        if p.stdout.strip() != expect:
            abort(f'{unit} {action}: expected {expect}, got {p.stdout.strip()}')


def load_devices():
    if not CONF.is_file() or not TOKENS.is_file() or TOKENS.stat().st_mode & 0o077:
        abort('Device config or private token file missing')
    obj = json.loads(CONF.read_text())
    entries = obj.get('devices', [])
    if len(entries) != 3 or {x.get('id') for x in entries} != set(EXPECTED):
        abort('Expected exactly three named nodes')
    token_map = dict(line.split('=', 1) for line in TOKENS.read_text().splitlines()
                     if '=' in line and not line.lstrip().startswith('#'))
    devices = []
    for e in entries:
        if e.get('enabled') is not True or e.get('transport') != 'http_lab':
            abort(f'{e["id"]}: disabled or unexpected transport')
        envname = e.get('token_env', '')
        token = token_map.get(envname, '')
        if not re.fullmatch('[A-Za-z0-9_-]{32,256}', token):
            abort(f'{e["id"]}: token unavailable')
        host = e.get('host', '')
        port = e.get('port', 8080)
        if not re.fullmatch(r'192\.168\.0\.\d{1,3}', str(host)) or port != 8080:
            abort(f'{e["id"]}: unexpected LAN endpoint')
        devices.append({'id': e['id'], 'host': host, 'port': port, 'token': token})
    return devices


def health(d):
    h = get(d, '/api/v1/health')
    if h.get('device_id') != EXPECTED[d['id']] or not str(h.get('node_version', '')).startswith('0.10.'):
        abort(f'{d["id"]}: wrong device identity or MeteorNode version')
    return h


def wait_recording(devices, timeout=25):
    running = set()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for d in devices:
            if d['id'] in running:
                continue
            try:
                h = health(d)
                state = str(h.get('state', ''))
                if state.startswith('recording'):
                    running.add(d['id'])
                    print('KAYIT_BASLADI:', d['id'], flush=True)
                elif state == 'error':
                    abort(f'{d["id"]}: camera error: {str(h.get("last_error", ""))[:140]}')
            except (OSError, TimeoutError):
                pass
        if len(running) == len(devices):
            return
        time.sleep(1)
    abort('Not recording: ' + ', '.join(set(EXPECTED) - running))


def stop_all(devices):
    failed = []
    for d in devices:
        try:
            post(d, '/api/v1/control/stop')
            print('DURDURMA_EMRI:', d['id'], flush=True)
        except Exception as exc:
            failed.append(d['id'])
            print('DURDURMA_HATASI:', d['id'], type(exc).__name__, str(exc)[:180], flush=True)
    return failed


def verify_ready(devices, before, timeout=70):
    passed = set()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for d in devices:
            if d['id'] in passed:
                continue
            try:
                h = health(d)
                if str(h.get('state', '')).startswith('recording'):
                    continue
                newer = segment_keys(segments(d)) - before[d['id']]
                if newer:
                    print('YENI_READY_MP4:', d['id'], 'adet='+str(len(newer)), flush=True)
                    passed.add(d['id'])
            except (OSError, TimeoutError):
                pass
        if len(passed) == len(devices):
            return
        time.sleep(2)
    abort('Not all cameras produced READY MP4: ' + str(sorted(set(EXPECTED) - passed)))


def main():
    service_preflight()
    devices = load_devices()
    before = {}
    for d in devices:
        h = health(d)
        state = str(h.get('state', ''))
        if state not in ('idle', 'armed_idle'):
            abort(f'{d["id"]} is not idle ({state}), recording untouched')
        if h.get('remote_camera_ready') is not True or h.get('remote_control_enabled') is not True:
            abort(f'{d["id"]}: camera/control not ready')
        if int(h.get('free_bytes') or 0) < 2 * 1024**3:
            abort(f'{d["id"]}: under 2 GB free')
        before[d['id']] = segment_keys(segments(d))
        print('HAZIR:', d['id'], 'READY='+str(len(before[d['id']])), flush=True)

    contacted = []
    failed_stop = []
    try:
        for d in devices:
            # Add before posting: if a network timeout occurs, still try to stop it.
            contacted.append(d)
            post(d, '/api/v1/control/start')
            print('BASLATMA_EMRI:', d['id'], 'HTTP_202', flush=True)
        wait_recording(devices)
        print('3_TELEFON_KAYITTA: 35_SANİYE', flush=True)
        time.sleep(DURATION)
    finally:
        if contacted:
            failed_stop = stop_all(contacted)

    if failed_stop:
        abort('Stop failed on: '+str(failed_stop)+'; inspect phones immediately')
    verify_ready(devices, before)
    print('SONUC: 3/3 MP4 READY, SILME YOK, TIMER KAPALI', flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('INTERRUPTED: stop commands attempted; inspect phone status', file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print('TEST_DURDU:', type(exc).__name__, str(exc), file=sys.stderr)
        sys.exit(2)
