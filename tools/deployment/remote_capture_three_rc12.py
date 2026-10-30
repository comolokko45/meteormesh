#!/usr/bin/env python3
"""One-shot remote Ruby/Surya camera proof and Viva reactivation for MeteorMesh RC1.2.

No video download/analysis, phone deletion, or systemd activation. This is an
explicit trusted-LAN experiment with the lab-only MeteorNode 0.10 fixed key.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

HOME = Path.home()
CONF = HOME / '.config/meteormesh'
DEVICES = CONF / 'devices-three-lab-rc12.json'
TOKEN_FILE = CONF / 'tokens.env'
ROOT = '/srv/meteor-data'
UUID = '444a8a6d-090a-456c-8a9d-d99665c32574'
TARGETS = {'ruby': '192.168.0.237', 'surya': '192.168.0.105'}
REC_SECONDS = 35


def fail(reason: str):
    raise RuntimeError('DURDURULDU: ' + reason)


def request(host: str, token: str, method: str, path: str, timeout: float = 6):
    conn = http.client.HTTPConnection(host, 8080, timeout=timeout)
    try:
        conn.request(method, path, headers={'Authorization': 'Bearer ' + token,
                                             'Content-Length': '0'} if method == 'POST' else
                    {'Authorization': 'Bearer ' + token}, body=b'' if method == 'POST' else None)
        res = conn.getresponse()
        payload = res.read(1024 * 1024)
        try:
            obj = json.loads(payload)
        except (ValueError, UnicodeError):
            obj = {'error': payload[:200].decode('utf-8', 'replace')}
        return res.status, obj
    finally:
        conn.close()


def json_ok(host, token, route):
    status, body = request(host, token, 'GET', route)
    if status != 200 or not isinstance(body, dict):
        fail(f'{host} {route}: HTTP {status} {body.get("error", "") if isinstance(body,dict) else ""}')
    return body


def post_ok(host, token, route):
    status, body = request(host, token, 'POST', route)
    if status != 202:
        fail(f'{host} {route}: HTTP {status}, {body.get("error", "unexpected response")}')
    return body


def ready_count(host, token):
    d = json_ok(host, token, '/api/v1/segments')
    if not isinstance(d.get('segments'), list):
        fail(f'{host} READY listesi gecersiz')
    return len(d['segments'])


def health(host, token, expected):
    d = json_ok(host, token, '/api/v1/health')
    if d.get('device_id') != expected or not str(d.get('node_version','')).startswith('0.10.'):
        fail(f'{host}: cihaz kimligi/surumu hatali: {d.get("device_id")}')
    return d


def discover_viva(token: str):
    def probe(host):
        try:
            status, obj = request(host, token, 'GET', '/api/v1/health', timeout=0.9)
            if status == 200 and obj.get('device_id') == 'viva' and str(obj.get('node_version','')).startswith('0.10.'):
                return host
        except (OSError, TimeoutError, ValueError):
            pass
        return None
    # First check its previous known address; then the trusted local /24.
    found = probe('192.168.0.216')
    if found:
        return found
    with ThreadPoolExecutor(max_workers=48) as executor:
        futures = [executor.submit(probe, f'192.168.0.{n}') for n in range(1, 255)
                   if n not in (216, 237, 105)]
        for future in as_completed(futures):
            if future.result():
                found = future.result()
                # All already-submitted requests may complete; no side effects.
                return found
    return None


def inactive_user_service():
    def cli(*args):
        x = subprocess.run(args, capture_output=True, text=True, timeout=12)
        return x.stdout.strip(), x.returncode
    for name in ('meteormesh-morning.timer', 'meteormesh-morning.service'):
        active, _ = cli('systemctl', '--user', 'is-active', name)
        if active != 'inactive':
            fail(f'{name} calisiyor veya durumu bilinmiyor: {active}')
    enabled, _ = cli('systemctl', '--user', 'is-enabled', 'meteormesh-morning.timer')
    if enabled != 'disabled':
        fail(f'sabah zamanlayicisi devre disi degil: {enabled}')
    state, code = cli('findmnt', '-rn', '-M', ROOT, '-o', 'TARGET,FSTYPE,UUID')
    if code != 0 or state != f'{ROOT} ext4 {UUID}':
        fail('HDD UUID veya mount dogrulamasi basarisiz')


def activate_viva(viva_ip: str):
    old = json.loads(DEVICES.read_text(encoding='utf-8'))
    rows = old.get('devices', [])
    if len(rows) != 3 or set(x.get('id') for x in rows) != {'cam-viva', 'cam-ruby', 'cam-surya'}:
        fail('uc cihazli beklenen config bulunamadi')
    for name, host in [('cam-ruby', TARGETS['ruby']), ('cam-surya', TARGETS['surya'])]:
        item = next(x for x in rows if x['id'] == name)
        if item.get('host') != host or not item.get('enabled') or item.get('transport') != 'http_lab':
            fail(f'{name} ayarlari baska bir sohbet tarafindan degistirilmis olabilir')
    viva = next(x for x in rows if x['id'] == 'cam-viva')
    if viva.get('transport') != 'http_lab':
        fail('Viva baglanti turu beklenenden farkli')
    if viva.get('host') == viva_ip and viva.get('enabled') is True:
        print('VIVA_ZATEN_AKTIF:', viva_ip)
        return
    new = json.loads(json.dumps(old))
    v = next(x for x in new['devices'] if x['id'] == 'cam-viva')
    v['host'] = viva_ip
    v['enabled'] = True
    if DEVICES.stat().st_mode & 0o077:
        fail('cihaz config izinleri beklenenden genis')
    backup = CONF / 'devices-three-lab-rc12.before-viva-enable.json'
    if backup.exists():
        fail(f'yedek zaten var, yeni yazma yok: {backup}')
    # Create backup without replacing current config, then atomically publish.
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as h:
        json.dump(old, h, ensure_ascii=False, indent=2)
        h.write('\n')
        h.flush()
        os.fsync(h.fileno())
    temp = None
    try:
        fd, temp = tempfile.mkstemp(prefix='.three-ready-', dir=str(CONF))
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as h:
            json.dump(new, h, indent=2, ensure_ascii=False)
            h.write('\n')
            h.flush()
            os.fsync(h.fileno())
        os.replace(temp, DEVICES)
        temp = None
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)
    print('UC_TELEFON_CONFIG_AKTIF:', viva_ip, TARGETS['ruby'], TARGETS['surya'])


def main():
    inactive_user_service()
    if not TOKEN_FILE.is_file() or TOKEN_FILE.stat().st_mode & 0o077:
        fail('tokens.env yok veya izinleri guvensiz')
    content = TOKEN_FILE.read_text(encoding='utf-8')
    matches = re.findall(r'^METEOR_NODE_VIVA_TEST_TOKEN=([A-Za-z0-9_-]{32,256})$', content, re.M)
    if len(matches) != 1:
        fail('mevcut lab anahtari bulunamadi')
    token = matches[0]
    if not DEVICES.is_file():
        fail('uc cihazli RC1.2 config bulunamadi')

    before = {}
    for name, host in TARGETS.items():
        h = health(host, token, name)
        if not h.get('remote_control_enabled') or not h.get('remote_camera_ready'):
            fail(f'{name}: uzaktan kamera servisi hazir degil')
        state = str(h.get('state',''))
        if state not in ('armed_idle', 'idle'):
            fail(f'{name}: mevcut kayit durumuna dokunulmadi: {state}')
        if int(h.get('free_bytes', 0)) < 2 * 1024**3:
            fail(f'{name}: bos alan 2 GiB altinda')
        before[name] = ready_count(host, token)
        print('HAZIR:', name, host, 'state='+state, 'READY='+str(before[name]), flush=True)

    started = []
    try:
        for name, host in TARGETS.items():
            post_ok(host, token, '/api/v1/control/start')
            started.append((name, host))
            print('KAYIT_EMRI:', name, 'HTTP_202', flush=True)

        # 202 only queues a command. Require actual 'recording:*' from both.
        limit = time.monotonic() + 22
        confirmed = set()
        while time.monotonic() < limit:
            for name, host in TARGETS.items():
                if name not in confirmed:
                    try:
                        d = health(host, token, name)
                        if str(d.get('state','')).startswith('recording:'):
                            confirmed.add(name)
                            print('KAYIT_BASLADI:', name, flush=True)
                        elif d.get('state') == 'error':
                            fail(f'{name} kamera hatasi: {d.get("last_error", "")}')
                    except (OSError, TimeoutError):
                        continue
            if len(confirmed) == len(TARGETS):
                break
            time.sleep(1)
        if len(confirmed) != len(TARGETS):
            fail('kayit baslamayan cihazlar: ' + str(sorted(set(TARGETS)-confirmed)))
        print(f'KAYIT_SURESI: {REC_SECONDS} saniye; iki cihaz paralel', flush=True)
        time.sleep(REC_SECONDS)
    finally:
        # Stop EVERY camera that received an accepted start, including on Ctrl+C.
        for name, host in started:
            try:
                post_ok(host, token, '/api/v1/control/stop')
                print('DURDURMA_EMRI:', name, flush=True)
            except Exception as exc:
                print('UYARI: DURDURMA_EMRI_BASARISIZ:', name, type(exc).__name__, str(exc)[:160], flush=True)

    deadline = time.monotonic() + 50
    completed = set()
    while time.monotonic() < deadline:
        for name, host in TARGETS.items():
            try:
                count = ready_count(host, token)
                d = health(host, token, name)
                if count > before[name] and not str(d.get('state','')).startswith('recording:'):
                    if name not in completed:
                        print('YENI_READY_MP4:', name, 'adet='+str(count-before[name]), flush=True)
                    completed.add(name)
            except (OSError, TimeoutError):
                pass
        if len(completed) == len(TARGETS):
            break
        time.sleep(2)
    if len(completed) != len(TARGETS):
        fail('Tum telefonlarda yeni READY MP4 teyit edilemedi: ' + str(sorted(completed)) +
             '; kayitlar telefonda korundu, yeniden baslatma yapma')

    viva_ip = discover_viva(token)
    if viva_ip:
        h = health(viva_ip, token, 'viva')
        print('VIVA_BULUNDU:', viva_ip, 'durum='+str(h.get('state')), flush=True)
        activate_viva(viva_ip)
    else:
        print('VIVA_HALEN_ERISILEMIYOR: Ruby+Surya serviste aktif kalir', flush=True)
    print('UZAKTAN_IKI_KAYIT_HAZIR; UC_CIHAZ_KONFIG_KONTROL_EDILDI; TIMER_KAPALI; VIDEO_SILINMEDI', flush=True)
    print('LAB_HTTP_SABIT_ANAHTAR: SADECE GUVENILIR_YEREL_AG', flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('KULLANICI_KESINTISI: kabul edilmis kayitlara durdurma emri gonderildi')
        raise SystemExit(130)
    except Exception as exc:
        print(type(exc).__name__ + ': ' + str(exc))
        raise SystemExit(2)
