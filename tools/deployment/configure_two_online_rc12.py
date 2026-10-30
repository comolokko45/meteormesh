#!/usr/bin/env python3
"""MeteorMesh RC1.2: attach verified Ruby/Surya to existing inactive user service.

This script does NOT start the service, enable the timer, download videos, or run detection.
Viva remains listed but disabled until detected. Lab HTTP, fixed-key; not production-ready.
"""
from __future__ import annotations
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

HOME = Path.home()
CONF = HOME / '.config/meteormesh'
SERVICE = HOME / '.config/systemd/user/meteormesh-morning.service.d/90-meteormesh-rc12.conf'
CURRENT = CONF / 'devices-viva-lab-rc12.json'
TARGET = CONF / 'devices-three-lab-rc12.json'
TOKEN = CONF / 'tokens.env'
RELEASE = HOME / '.local/share/meteormesh/releases/rc12-20261030'
ROOT = '/srv/meteor-data'
UUID = '444a8a6d-090a-456c-8a9d-d99665c32574'
NEARBY = [('cam-ruby', 'ruby', '192.168.0.237'), ('cam-surya', 'surya', '192.168.0.105')]

def abort(msg):
    raise SystemExit('KURULUM_DURDU: ' + msg)

def command(*args, timeout=12):
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout)

def http_json(ip, endpoint, token):
    conn = http.client.HTTPConnection(ip, 8080, timeout=5)
    try:
        conn.request('GET', endpoint, headers={'Authorization': 'Bearer ' + token})
        resp = conn.getresponse()
        body = resp.read(1024 * 1024)
        if resp.status != 200:
            abort(f'{ip} {endpoint}: HTTP {resp.status}; cihaz baglantisi/anahtari yanlis')
        return json.loads(body)
    finally:
        conn.close()

def atomic_write(path, content):
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

def main():
    if os.geteuid() == 0:
        abort('root olarak calistirma')
    if not (RELEASE / 'MeteorHub/meteor_morning.py').is_file():
        abort('RC1.2 kalici surumu yok')
    if not CURRENT.is_file() or not SERVICE.is_file() or not TOKEN.is_file():
        abort('RC1.2 servis veya gizli anahtar kurulumu eksik')
    if TOKEN.stat().st_mode & 0o077:
        abort('token dosyasi grup/diger kullanicilara acik')
    mount = command('findmnt', '-rn', '-M', ROOT, '-o', 'TARGET,FSTYPE,UUID')
    if mount.returncode != 0 or mount.stdout.strip() != f'{ROOT} ext4 {UUID}':
        abort('HDD mount/UUID dogrulanamadi')
    for unit in ['meteormesh-morning.timer', 'meteormesh-morning.service']:
        r = command('systemctl', '--user', 'is-active', unit)
        if r.stdout.strip() != 'inactive':
            abort(f'{unit} beklenmedik durumda: {r.stdout.strip()}')
    en = command('systemctl', '--user', 'is-enabled', 'meteormesh-morning.timer')
    if en.stdout.strip() != 'disabled':
        abort('sabah timer disabled degil')

    matches = re.findall(r'^METEOR_NODE_VIVA_TEST_TOKEN=(\S+)$', TOKEN.read_text(), re.M)
    if len(matches) != 1 or len(matches[0]) < 32:
        abort('mevcut laboratuvar tokeni okunamiyor')
    token = matches[0]  # NEVER log the token
    for configured_id, reported_id, ip in NEARBY:
        health = http_json(ip, '/api/v1/health', token)
        if health.get('device_id') != reported_id:
            abort(f'{ip}: cihaz kimligi beklenenden farkli: {health.get("device_id")}')
        if not str(health.get('node_version', '')).startswith('0.10.'):
            abort(f'{ip}: MeteorNode 0.10 degil')
        segments = http_json(ip, '/api/v1/segments', token)
        if not isinstance(segments.get('segments'), list):
            abort(f'{ip}: READY listesi gecersiz')
        print(f'DOGRULANDI: {configured_id} {ip} | READY={len(segments["segments"])}')

    old = json.loads(CURRENT.read_text())
    entries = old.get('devices', [])
    if len(entries) != 1 or entries[0].get('id') != 'cam-viva':
        abort('Viva yapilandirmasi beklenen tek cihazli surum degil')
    if entries[0].get('host') != '192.168.0.216':
        abort('Viva IP bilinen degerle uyusmuyor; otomatik degistirmiyorum')
    common = entries[0].get('token_env')
    if common != 'METEOR_NODE_VIVA_TEST_TOKEN':
        abort('laboratuvar token ortam degiskeni farkli')
    viva = dict(entries[0]); viva['enabled'] = False
    rows = [viva] + [dict(id=name, enabled=True, host=ip, port=8080,
                          transport='http_lab', token_env=common)
                     for name, _, ip in NEARBY]
    text = json.dumps({'devices': rows}, indent=2, ensure_ascii=False) + '\n'
    if TARGET.exists() and TARGET.read_text() != text:
        abort('uc cihaz yapilandirmasi zaten var ve farkli; uzerine yazmiyorum')
    if not TARGET.exists():
        with TARGET.open('x', encoding='utf-8') as out:
            out.write(text)
        TARGET.chmod(0o600)

    repo = '/opt/meteormesh/MetDetPy'
    detector = repo + '/config/m3det_normal.json'
    cmd = ['/opt/meteormesh/venv-metdetpy/bin/python', '-B', str(RELEASE / 'MeteorHub/meteor_morning.py'),
           'plan', '--config', str(TARGET), '--data-dir', ROOT,
           '--metdet-repo', repo, '--metdet-config', detector]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
               METEORMESH_DETECTOR_PYTHON='/opt/meteormesh/venv-metdetpy/bin/python',
               METEORMESH_DETECTOR_CPU_MODE='1')
    check = subprocess.run(cmd, text=True, capture_output=True, timeout=35, env=env)
    if check.returncode != 0:
        abort('RC1.2 PLAN basarisiz: ' + check.stderr[-500:])
    data = json.loads(check.stdout)
    if data.get('device_ids') != ['cam-ruby', 'cam-surya'] or not data.get('config_ready_for_live_run'):
        abort('RC1.2 cihaz/plani uyusmuyor: ' + str(data.get('device_ids')))
    print('RC12_PLAN_OK: ruby+surya etkin, viva kayitli/pasif')

    service_text = SERVICE.read_text()
    if str(TARGET) in service_text:
        print('SERVIS_YAPILANDIRMASI_ZATEN_HAZIR')
    else:
        needle = '--config ' + str(CURRENT)
        if service_text.count(needle) != 1:
            abort('mevcut servis ExecStart config yolu beklenenden farkli')
        backup = RELEASE / '90-meteormesh-rc12-before-three.conf'
        if backup.exists() and backup.read_text() != service_text:
            abort('onceki servis yedegi farkli; otomatik degistirmiyorum')
        if not backup.exists():
            with backup.open('x') as out:
                out.write(service_text)
            backup.chmod(0o600)
        atomic_write(SERVICE, service_text.replace(needle, '--config ' + str(TARGET)))
        reload = command('systemctl', '--user', 'daemon-reload')
        if reload.returncode != 0:
            atomic_write(SERVICE, service_text)
            command('systemctl', '--user', 'daemon-reload')
            abort('daemon-reload basarisiz, eski servis ayari geri alindi')
        print('SERVIS_RC12_IKI_TELEFON_ICIN_HAZIR')

    effective = command('systemctl', '--user', 'show', 'meteormesh-morning.service',
                        '-p', 'ExecStart', timeout=12)
    if str(TARGET) not in effective.stdout:
        abort('systemd etkin komut yolunu dogrulayamadim')
    print('VIVA=OFFLINE; RUBY=ACTIVE; SURYA=ACTIVE')
    print('TIMER_DISABLED; NO_VIDEO_TRANSFER; NO_ANALYSIS')
    print('LAB_HTTP_FIXED_KEY: NOT PRODUCTION SECURE')
    print('TWO_NODE_SERVICE_PREPARED')

if __name__ == '__main__':
    main()
