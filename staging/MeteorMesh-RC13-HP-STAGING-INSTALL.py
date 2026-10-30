#!/usr/bin/env python3
"""Staging only. No active code, timer, database, MP4, phone modifications."""
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
import zipfile
from pathlib import Path

HOME = Path.home()
SOURCE = HOME / '.local/share/meteormesh/releases/rc12-20261030/MeteorHub'
STAGE = HOME / '.local/share/meteormesh/staging/fullnight'
POLICY = STAGE / 'night-scope-v1.json'
URL = ('https://raw.githubusercontent.com/comolokko45/meteormesh/'
       'meteormesh-night-scope-review-20261030/staging/'
       'MeteorMesh-RC1.3-Night-Scope-STAGING-2026-10-30.zip.b64')
B64_SHA = '0b5e5fa13823fc531516c56f7dbe4f26db327b3c8495284fcf75b8520e012f74'
ZIP_SHA = '125fa63e663146bbe9e5c048be46d5ce185a74a11aa507cf510e55cbd1f39ac6'
FILES = {'night_scope.py', 'meteor_morning.py', 'meteormesh_hub.py',
         'hub_workflow.py', 'hub_events.py', 'test_night_scope.py'}

def sha(data):
    return hashlib.sha256(data).hexdigest()

def state(action, unit):
    p = subprocess.run(['systemctl', '--user', action, unit],
                       text=True, capture_output=True, timeout=8)
    return p.stdout.strip()

print('===== RC1.3 GUVENLI SSD STAGING =====')
if state('is-enabled', 'meteormesh-morning.timer') != 'disabled':
    raise SystemExit('DUR: Sabah timer kapali degil')
if state('is-active', 'meteormesh-morning.service') != 'inactive':
    raise SystemExit('DUR: Sabah servisi calisiyor')
if not SOURCE.is_dir() or SOURCE.is_symlink():
    raise SystemExit('DUR: RC1.2 kaynak dizini bulunamadi')
if POLICY.is_symlink() or not POLICY.is_file():
    raise SystemExit('DUR: DISARMED policy eksik')
policy = json.loads(POLICY.read_text())
if policy.get('status') != 'DISARMED' or policy.get('automatic_run_allowed') is not False:
    raise SystemExit('DUR: Policy zaten etkin gorunuyor')
count = sum(len(d) for d in policy.get('excluded_segments', {}).values())
if count != 14:
    raise SystemExit('DUR: 14 eski kayit kimligi dogrulanamadi')
print('Eski 14 video kaydi: haric tutma listesinde')

with urllib.request.urlopen(URL, timeout=35) as conn:
    encoded = conn.read(100000)
if sha(encoded) != B64_SHA:
    raise SystemExit('DUR: GitHub dosyasi SHA256 uyusmuyor')
payload = base64.b64decode(encoded, validate=False)
if sha(payload) != ZIP_SHA:
    raise SystemExit('DUR: kaynak ZIP SHA256 uyusmuyor')

with zipfile.ZipFile(io.BytesIO(payload)) as z:
    if z.testzip() is not None or set(z.namelist()) != FILES | {'MANIFEST.json','README.txt'}:
        raise SystemExit('DUR: Paket dosya listesi/CRC uyusmuyor')
    manifest = json.loads(z.read('MANIFEST.json'))
    if manifest.get('kind') != 'METEORMESH_RC13_STAGING_ONLY':
        raise SystemExit('DUR: Yanlis paket')
    for name, expected in manifest['base_hub_files_sha256'].items():
        if sha((SOURCE/name).read_bytes()) != expected:
            raise SystemExit('DUR: RC1.2 surum farki: '+name)
    for name in FILES:
        if sha(z.read(name)) != manifest['patched_files_sha256'][name]:
            raise SystemExit('DUR: Yama dosyasi hash farki: '+name)
    # Unique SSD staging directory; no production release or HDD writes.
    STAGE.mkdir(parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix='night-rc13-', dir=STAGE)) / 'MeteorHub'
    shutil.copytree(SOURCE, target)
    for name in FILES:
        (target/name).write_bytes(z.read(name))

print('Paket SHA256: DOGRU')
print('RC1.2 ana surum: DEGISTIRILMEDI')
env = os.environ.copy()
env['PYTHONDONTWRITEBYTECODE'] = '1'
p = subprocess.run([sys.executable, '-B', '-m', 'unittest', '-q', 'test_night_scope'],
                   cwd=target, env=env, capture_output=True, text=True, timeout=45)
print('Gece kapsam testleri:', 'GECTI' if p.returncode == 0 else 'BASARISIZ')
print((p.stdout + p.stderr)[-1500:])
if p.returncode:
    raise SystemExit('DUR: Staging testleri basarisiz; aktivasyon yok')
# Explicit check DISARMED rejects; no data root access occurs.
sys.path.insert(0, str(target))
from night_scope import load_night_scope
try:
    load_night_scope(POLICY)
except RuntimeError as exc:
    if 'not ARMED' not in str(exc):
        raise
    print('DISARMED KORUMASI: DOGRULANDI')
else:
    raise SystemExit('DUR: DISARMED policy beklenmedik sekilde kabul edildi')
print('STAGING:', target)
print('URETIM: DEGISTIRILMEDI; TIMER: KAPALI')
print('===== RC1.3 STAGING KABUL TAMAMLANDI =====')