#!/usr/bin/env python3
"""Read-only MeteorMesh 0.13 Hub acceptance before phones are physically present.

The live control service receives GET /v1/status ONLY. All simulated heartbeats,
commands, failures, ACKs and reconnections occur in a new, isolated ControlState
Python object in this local process. No live device is spoofed, no SQLite, no videos.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import ssl
import stat
import subprocess
import sys
import time
from urllib.request import urlopen

HOME = Path.home()
ROOT = HOME / '.local/share/meteormesh/control-013'
RELEASE = HOME / '.local/share/meteormesh/releases/control-013-20261101'
CERT_FP = 'aa2f35e659a795bbd50ba8fade8de916ae7308c5cef3247a3fba74683c282c7b'
CAMERAS = ('cam-viva', 'cam-ruby', 'cam-surya')

class AcceptanceError(RuntimeError):
    pass

def require(ok, message):
    if not ok:
        raise AcceptanceError(message)

def private_file(path):
    require(path.is_file() and not path.is_symlink(), 'MISSING_OR_SYMLINK: ' + path.name)
    mode = path.stat().st_mode
    require(stat.S_ISREG(mode) and not mode & 0o077, 'UNSAFE_PERMISSIONS: ' + path.name)
    require(path.stat().st_uid == os.getuid(), 'UNEXPECTED_FILE_OWNER: ' + path.name)

def cmd(*args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=10)
    require(p.returncode == 0, 'COMMAND_FAILED: ' + str(args[:3]))
    return p.stdout.strip()

def run():
    require(os.geteuid() != 0, 'DO_NOT_RUN_AS_ROOT')
    require(ROOT.is_dir() and not ROOT.is_symlink() and RELEASE.is_dir() and not RELEASE.is_symlink(),
            'CONTROL_013_MISSING')
    require(not ROOT.stat().st_mode & 0o077, 'CONTROL_DIR_PERMISSIONS_UNSAFE')
    for f in ('config.json', 'server.crt', 'server.key'):
        private_file(ROOT / f)
    config = json.loads((ROOT / 'config.json').read_text())
    require(isinstance(config, dict) and config.get('port') == 8443 and
            config.get('bind') == '0.0.0.0' and set(config.get('tokens', {})) == set(CAMERAS),
            'CONTROL_BASELINE_UNEXPECTED')
    require(config.get('cert') == str(ROOT / 'server.crt') and
            config.get('key') == str(ROOT / 'server.key'), 'CERT_PATH_CHANGED')
    tokens = config['tokens']
    for device in CAMERAS:
        expected = hashlib.sha256(f'meteormesh.control013.pin.v1:{device}:0000'.encode()).hexdigest()
        require(isinstance(tokens[device], str) and
                secrets.compare_digest(tokens[device], expected), 'PIN0000_MISMATCH: ' + device)
    print('PIN_0000: PASS; ALL_THREE_CAMERAS', flush=True)
    der = ssl.PEM_cert_to_DER_cert((ROOT / 'server.crt').read_text())
    actual = hashlib.sha256(der).hexdigest()
    require(secrets.compare_digest(CERT_FP, actual), 'TLS_CERTIFICATE_CHANGED')
    require(cmd('systemctl', '--user', 'is-active', 'meteormesh-control-013.service') == 'active',
            'CONTROL_SERVICE_NOT_ACTIVE')
    require(cmd('systemctl', '--user', 'is-enabled', 'meteormesh-control-013.service') == 'enabled',
            'CONTROL_SERVICE_NOT_ENABLED')
    with urlopen('https://127.0.0.1:8443/v1/status',
                 context=ssl.create_default_context(cafile=str(ROOT / 'server.crt')), timeout=5) as r:
        require(r.status == 200, 'HTTPS_STATUS_HTTP_ERROR')
        raw = r.read(4097)
        require(len(raw) <= 4096, 'HTTPS_STATUS_OVERSIZE')
        live = json.loads(raw)
    require(live.get('schema') == 'meteormesh.control-status.v1' and
            set(live.get('devices', {})) == set(CAMERAS), 'HTTPS_STATUS_SCHEMA_ERROR')
    print('HTTPS_8443_AND_CERT: PASS', flush=True)
    # Import only the definitions, not its command-line main; this makes a NEW
    # ControlState with entirely separate tokens and memory from the live daemon.
    source = RELEASE / 'server.py'
    private_file(source)
    spec = importlib.util.spec_from_file_location('mm013_abi_test', source)
    require(spec is not None and spec.loader is not None, 'CONTROL_CODE_UNREADABLE')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    virtual = [time.time()]
    isolated_keys = {d: secrets.token_hex(32) for d in CAMERAS}
    state = module.ControlState(isolated_keys, clock=lambda: virtual[0])

    def heartbeat(dev, nonce=None, ack=None, key=None):
        payload = {
            'device_id': dev,
            'timestamp_ms': round(virtual[0]*1000),
            'nonce': nonce or secrets.token_hex(16),
            'acked_command_id': ack,
            'status': {
                'camera_state': 'armed_idle', 'lan_listening': True,
                'ready_segments': 2, 'free_mib': 1024,
                'app_version': 'offline-simulator', 'lan_ip': '192.168.0.200'
            }
        }
        return state.heartbeat(dev, payload, 'Bearer ' + (key or isolated_keys[dev]))

    for d in CAMERAS:
        code, _ = heartbeat(d)
        require(code == 200, 'SIM_HEARTBEAT_REJECTED: ' + d)
    code, _ = heartbeat('cam-ruby', key=isolated_keys['cam-viva'])
    require(code == 401, 'SIM_WRONG_DEVICE_KEY_ACCEPTED')
    nonce = secrets.token_hex(16)
    require(heartbeat('cam-ruby', nonce=nonce)[0] == 200, 'SIM_NONCE_FIRST_FAILED')
    require(heartbeat('cam-ruby', nonce=nonce)[0] == 409, 'SIM_REPLAY_NOT_REJECTED')
    require(state.queue_command('cam-ruby', 'delete')[0] == 400, 'SIM_UNSAFE_COMMAND_ALLOWED')
    code, result = state.queue_command('cam-ruby', 'restart_listener')
    require(code == 202 and 'command_id' in result, 'SIM_COMMAND_NOT_QUEUED')
    cmdid = result['command_id']
    code, response = heartbeat('cam-ruby')
    require(code == 200 and response.get('command', {}).get('id') == cmdid, 'SIM_COMMAND_NOT_DELIVERED')
    code, _ = heartbeat('cam-ruby', ack=cmdid)
    require(code == 200 and not state.status()['devices']['cam-ruby']['command_pending'],
            'SIM_ACK_NOT_PROCESSED')
    virtual[0] += 170
    require(heartbeat('cam-viva')[0] == 200, 'SIM_INDEPENDENT_VIVA_UPDATE_FAILED')
    virtual[0] += 11
    results = state.status()['devices']
    require(results['cam-viva']['online'] and not results['cam-ruby']['online'] and
            not results['cam-surya']['online'], 'SIM_INDEPENDENCE_OR_OFFLINE_DETECTION_FAILED')
    print('SIM_THREE_CAMERAS: PASS', flush=True)
    print('SIM_TOKEN_REPLAY_COMMAND_ACK_INDEPENDENCE: PASS', flush=True)
    print('LIVE_DEVICE_STATES: ' + ', '.join(d + '=' + ('ONLINE' if live['devices'][d].get('online') else 'OFFLINE')
                                            for d in CAMERAS), flush=True)
    print('MM013_OFFLINE_ACCEPTANCE: PASS', flush=True)
    print('PRODUCTION_HEARTBEATS_SENT: 0; LIVE_CONFIG_DB_VIDEOS: UNCHANGED', flush=True)
    print('PHONE_APK_BUILD_AND_REAL_LAN_ACCEPTANCE: STILL_PENDING', flush=True)

if __name__ == '__main__':
    try:
        run()
    except (AcceptanceError, OSError, ValueError, ssl.SSLError, json.JSONDecodeError,
            subprocess.SubprocessError, AttributeError) as e:
        print('MM013_OFFLINE_ACCEPTANCE: STOPPED ' + type(e).__name__ + ': ' + str(e)[:180], file=sys.stderr)
        sys.exit(2)
