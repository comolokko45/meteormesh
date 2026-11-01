#!/usr/bin/env python3
"""MeteorMesh HP G62 local CLI: inspect phones and queue one approved LAN recovery.

No new daemon, no background processes, no direct phone access, no delete/camera
control, no passwords in CLI. Uses the existing verified local HTTPS control service.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import ssl
import sys
import time
from urllib import request, error

ROOT = Path.home() / '.local/share/meteormesh/control-013'
URL = 'https://127.0.0.1:8443'
DEVICES = ('cam-viva', 'cam-ruby', 'cam-surya')

class ControlError(Exception):
    pass


def call(path: str, payload=None) -> tuple[int, dict]:
    cert = ROOT / 'server.crt'
    if cert.is_symlink() or not cert.is_file():
        raise ControlError('LOCAL_CERT_MISSING_OR_SYMLINK')
    ctx = ssl.create_default_context(cafile=str(cert))
    body = json.dumps(payload, separators=(',', ':')).encode() if payload is not None else None
    req = request.Request(URL + path, data=body, method='POST' if body else 'GET',
                          headers={'Content-Type': 'application/json'} if body else {})
    try:
        with request.urlopen(req, context=ctx, timeout=5) as response:
            content = response.read(8192)
            if response.read(1):
                raise ControlError('RESPONSE_TOO_LARGE')
            data = json.loads(content.decode('utf-8'))
            if not isinstance(data, dict):
                raise ControlError('INVALID_SERVER_RESPONSE')
            return response.status, data
    except error.HTTPError as exc:
        raise ControlError('HTTP_' + str(exc.code)) from exc
    except (OSError, ValueError, ssl.SSLError) as exc:
        raise ControlError('HTTPS_CONNECTION_FAILED: ' + type(exc).__name__) from exc


def status() -> dict:
    code, data = call('/v1/status')
    if code != 200 or data.get('schema') != 'meteormesh.control-status.v1':
        raise ControlError('UNEXPECTED_STATUS_RESPONSE')
    if not isinstance(data.get('devices'), dict):
        raise ControlError('INVALID_DEVICE_MAP')
    return data['devices']


def show():
    devices = status()
    for name in DEVICES:
        obj = devices.get(name,{})
        print(name + ': ' + ('ONLINE' if obj.get('online') else 'OFFLINE')
              + (' | pending command' if obj.get('command_pending') else ''))


def queue(device: str, wait: bool) -> None:
    items = status()
    if device not in items or not items[device].get('online'):
        raise ControlError('PHONE_OFFLINE: ' + device)
    if items[device].get('command_pending'):
        raise ControlError('COMMAND_ALREADY_PENDING: ' + device)
    code, result = call('/v1/command', {'device_id': device, 'action': 'restart_listener'})
    if code != 202 or not result.get('queued') or not isinstance(result.get('command_id'), str):
        raise ControlError('COMMAND_NOT_QUEUED')
    print('QUEUED_RESTART_LISTENER: ' + device)
    print('The phone will receive this at its next heartbeat; not yet executed.')
    if not wait:
        return
    started = time.monotonic()
    saw_offline = False
    while time.monotonic() - started < 95:
        time.sleep(5)
        current = status().get(device, {})
        if not current.get('command_pending'):
            print('COMMAND_ACK_RECEIVED: ' + device)
            print('Note: an ACK is not proof the HTTP listener is reachable from the hub.')
            return
        if not current.get('online'):
            saw_offline = True
    raise ControlError('ACK_NOT_RECEIVED_WITHIN_95S' + ('_PHONE_OFFLINE' if saw_offline else ''))


def main():
    parser = argparse.ArgumentParser(description='MeteorMesh local control; no new service')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    re = sub.add_parser('restart-listener')
    re.add_argument('device', choices=DEVICES)
    re.add_argument('--wait', action='store_true', help='Wait up to 95 seconds for command ACK')
    args = parser.parse_args()
    if args.command == 'status':
        show()
    else:
        queue(args.device, args.wait)


if __name__ == '__main__':
    try:
        main()
    except ControlError as exc:
        print('METEOR_CONTROL_ERROR:', exc, file=sys.stderr)
        sys.exit(2)
