#!/usr/bin/python3
"""Store one ADB-extracted MeteorNode storage key without printing it."""
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

DEVICES = {'cam-surya': 'METEOR_NODE_SURYA_STORAGE_ADMIN_KEY',
           'cam-ruby': 'METEOR_NODE_RUBY_STORAGE_ADMIN_KEY',
           'cam-viva': 'METEOR_NODE_VIVA_STORAGE_ADMIN_KEY'}
TARGET = Path.home() / '.config/meteormesh/storage-admin.env'
HEX = re.compile(r'^[0-9a-f]{64}$')

def fail(code):
    print(code, file=sys.stderr)
    raise SystemExit(2)

if len(sys.argv) != 2 or sys.argv[1] not in DEVICES:
    fail('USAGE: meteor-store-storage-key.py cam-surya|cam-ruby|cam-viva < key-on-stdin')
raw = sys.stdin.buffer.read(256).strip()
try:
    key = raw.decode('ascii')
except UnicodeDecodeError:
    fail('INVALID_KEY_ENCODING')
if not HEX.fullmatch(key):
    fail('INVALID_KEY_FORMAT')
if TARGET.parent.is_symlink() or not TARGET.parent.is_dir():
    fail('TARGET_DIRECTORY_UNSAFE')
if TARGET.is_symlink():
    fail('TARGET_IS_SYMLINK')
if TARGET.exists():
    st = TARGET.stat(follow_symlinks=False)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_mode & 0o077:
        fail('TARGET_PERMISSIONS_OR_TYPE')
    lines = TARGET.read_text(encoding='ascii').splitlines()
else:
    lines = []
values = {'METEORMESH_PHONE_PURGE': '1'}
for line in lines:
    if not line:
        continue
    if '=' not in line:
        fail('INVALID_EXISTING_ENV_FILE')
    name, value = line.split('=', 1)
    if name == 'METEORMESH_PHONE_PURGE':
        if value != '1':
            fail('PURGE_FLAG_CHANGED')
        values[name] = value
    elif name in DEVICES.values() and HEX.fullmatch(value):
        values[name] = value
    else:
        fail('UNEXPECTED_EXISTING_ENV_ENTRY')
values[DEVICES[sys.argv[1]]] = key
content = ''.join(f'{name}={value}\n' for name, value in values.items()).encode('ascii')
fd, temp = tempfile.mkstemp(prefix='.storage-admin-', suffix='.tmp', dir=TARGET.parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'wb') as out:
        out.write(content)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, TARGET)
    dirfd = os.open(TARGET.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(dirfd)
    finally:
        os.close(dirfd)
finally:
    if os.path.exists(temp):
        os.unlink(temp)
print(f'STORAGE_ADMIN_KEY_STORED {sys.argv[1]}')
