#!/usr/bin/env python3
"""Explicitly paired, LAN-only MeteorNode remote control; no OS shell or deletion."""
import argparse
import http.client
import json
import os

# Embedded test key matches MeteorNode 0.10; NOT a secret. Trusted LAN only.
DEFAULT_TEST_KEY = "00000000000000000000000000000000"
from urllib.parse import urlsplit


def request(host, token, method, endpoint, data=None, timeout=10):
    """Intentionally uses plain LAN HTTP; do not expose beyond a trusted Wi-Fi."""
    conn = http.client.HTTPConnection(host, 8080, timeout=timeout)
    payload = json.dumps(data).encode() if data is not None else b""
    headers = {"Authorization": "Bearer " + token}
    if method == "POST":
        headers["Content-Length"] = str(len(payload))
        if data is not None:
            headers["Content-Type"] = "application/json"
    try:
        conn.request(method, endpoint, body=payload if method == "POST" else None, headers=headers)
        response = conn.getresponse()
        raw = response.read(1_000_000)
        try:
            result = json.loads(raw)
        except ValueError:
            result = {"raw": raw.decode("utf-8", "replace")}
        return response.status, result
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="MeteorNode LAN remote app control")
    parser.add_argument("--host", required=True, help="Viva LAN address, e.g. 192.168.0.216")
    parser.add_argument("--token-env", default="METEORNODE_PAIRING_TOKEN")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("status", "full-status", "permissions", "settings", "start", "stop"):
        sub.add_parser(name)
    cfg = sub.add_parser("set")
    cfg.add_argument("--profile", choices=("auto", "manual_800", "manual_1600"))
    cfg.add_argument("--bitrate-mbps", type=int, choices=(2, 4, 6, 8, 12))
    cfg.add_argument("--segment-seconds", type=int, choices=(60, 120, 300, 600, 900))
    args = parser.parse_args()
    token = os.environ.get(args.token_env) or DEFAULT_TEST_KEY
    routes = {
        "status": ("GET", "/api/v1/health", None),
        "full-status": ("GET", "/api/v1/status", None),
        "permissions": ("GET", "/api/v1/permissions", None),
        "settings": ("GET", "/api/v1/settings", None),
        "start": ("POST", "/api/v1/control/start", None),
        "stop": ("POST", "/api/v1/control/stop", None),
    }
    if args.action == "set":
        obj = {}
        if args.profile is not None:
            obj["profile"] = args.profile
        if args.bitrate_mbps is not None:
            obj["bitrate_mbps"] = args.bitrate_mbps
        if args.segment_seconds is not None:
            obj["segment_seconds"] = args.segment_seconds
        if not obj:
            parser.error("set needs at least one setting")
        method, route, body = "POST", "/api/v1/settings", obj
    else:
        method, route, body = routes[args.action]
    status, result = request(args.host, token, method, route, body)
    print(json.dumps({"http_status": status, **result}, indent=2, ensure_ascii=False))
    if status >= 400:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
