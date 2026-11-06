#!/usr/bin/env python3
"""Bounded boot gate for systemd user services on the G62."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time


def check_ready() -> tuple[bool, str]:
    networkd = subprocess.run(
        ["/usr/bin/systemctl", "is-active", "--quiet", "systemd-networkd.service"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
    )
    if networkd.returncode:
        return False, "systemd-networkd is not active"
    route = subprocess.run(
        ["/usr/sbin/ip", "-4", "route", "show", "default"],
        capture_output=True, text=True, timeout=5,
    )
    if route.returncode or not any(
            line.startswith("default ") for line in route.stdout.splitlines()):
        return False, "IPv4 default route is not ready"
    try:
        ts = subprocess.run(
            ["/usr/bin/tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=8,
        )
        if ts.returncode:
            return False, "Tailscale status is unavailable"
        state = json.loads(ts.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False, "Tailscale status is unavailable"
    self_state = state.get("Self") or {}
    if state.get("BackendState") != "Running" or self_state.get("Online") is not True:
        return False, "Tailscale backend is not online"
    return True, "network route and Tailscale are ready"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--interval", type=int, default=5)
    args = parser.parse_args()
    if not 0 <= args.timeout <= 3600 or not 1 <= args.interval <= 60:
        parser.error("timeout must be 0..3600 and interval 1..60")
    deadline = time.monotonic() + args.timeout
    last_message = 0.0
    while True:
        ready, reason = check_ready()
        if ready:
            print("NETWORK_READY: network route and Tailscale are ready", flush=True)
            return 0
        now = time.monotonic()
        if now >= deadline:
            print(f"NETWORK_NOT_READY: {reason}; retry on the next systemd run",
                  file=sys.stderr, flush=True)
            return 1
        if now - last_message >= 30:
            print(f"NETWORK_WAIT: {reason}", flush=True)
            last_message = now
        time.sleep(min(args.interval, max(0.0, deadline - now)))


if __name__ == "__main__":
    raise SystemExit(main())
