#!/usr/bin/env python3
"""Wait for a direct route to any configured phone; capture handles each device."""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import subprocess
import sys
import time

CONFIG = Path.home() / '.config/meteormesh/devices-three-lab-rc12.json'


def configured_hosts(path: Path) -> list[str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError('DEVICE_CONFIG_MISSING_OR_UNSAFE')
    doc = json.loads(path.read_text(encoding='utf-8'))
    devices = doc.get('devices') if isinstance(doc, dict) else None
    if not isinstance(devices, list):
        raise ValueError('DEVICE_CONFIG_INVALID')
    hosts = []
    for device in devices:
        if not isinstance(device, dict) or not device.get('enabled', True):
            continue
        if device.get('transport') != 'http_lab' or device.get('port') != 8080:
            raise ValueError('UNSUPPORTED_CAPTURE_ENDPOINT')
        addr = ipaddress.ip_address(device.get('host', ''))
        if addr.version != 4 or not addr.is_private or addr.is_loopback or addr.is_link_local:
            raise ValueError('UNSAFE_CAPTURE_ENDPOINT')
        hosts.append(str(addr))
    if not hosts:
        raise ValueError('NO_CONFIGURED_CAPTURE_ENDPOINTS')
    return hosts


def direct_route_available(hosts: list[str], runner=subprocess.run) -> bool:
    for host in hosts:
        try:
            result = runner(['/usr/sbin/ip', '-j', '-4', 'route', 'get', host],
                            capture_output=True, text=True, timeout=3, check=False)
            routes = json.loads(result.stdout) if result.returncode == 0 else []
            if not isinstance(routes, list):
                continue
            for route in routes:
                if (isinstance(route, dict) and route.get('dst') == host and
                        isinstance(route.get('dev'), str) and
                        route['dev'] not in {'lo', 'tailscale0'} and
                        not route.get('gateway') and route.get('prefsrc')):
                    return True
        except (OSError, subprocess.SubprocessError, ValueError, TypeError):
            continue
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--interval', type=int, default=5)
    args = parser.parse_args()
    if not 0 <= args.timeout <= 3600 or not 1 <= args.interval <= 60:
        parser.error('timeout must be 0..3600 and interval 1..60')
    hosts = configured_hosts(CONFIG)
    deadline = time.monotonic() + args.timeout
    while True:
        if direct_route_available(hosts):
            print('CAPTURE_LAN_READY: direct route to configured phone network')
            return 0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print('CAPTURE_LAN_NOT_READY: retry on next capture tick', file=sys.stderr)
            return 1
        time.sleep(min(args.interval, remaining))


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print('CAPTURE_LAN_GATE_ERROR:', str(exc), file=sys.stderr)
        raise SystemExit(1)
