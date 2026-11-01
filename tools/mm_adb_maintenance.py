#!/usr/bin/env python3
"""MeteorMesh: consent-based ADB maintenance for the three owned Android nodes.

No background service, no ADB pairing bypass, no arbitrary shell commands.
Only a known MeteorNode APK hash may be installed; uninstall requires a typed
per-device confirmation because Android deletes app-private recordings.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

PACKAGE = "org.meteormesh.node.compactfixedtest"
# Published 2026-11-01 single-APK build, not the old isolated test APK.
RELEASE_SHA256 = "1f725502df4b20c19e3770b255adf8a2db47a87a52bedb651cceba7393688980"
DEVICES = {"cam-viva": "viva", "cam-ruby": "ruby", "cam-surya": "surya"}


def adb(*args: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ADB_TIMEOUT") from exc
    except OSError as exc:
        raise RuntimeError("ADB_NOT_AVAILABLE") from exc


def installed(serial: str) -> bool:
    result = adb("-s", serial, "shell", "pm", "path", PACKAGE)
    if result.returncode:
        raise RuntimeError("PACKAGE_QUERY_FAILED: " + result.stderr.strip()[:200])
    return "package:" in result.stdout


def connected() -> dict[str, str]:
    result = adb("devices")
    if result.returncode:
        raise RuntimeError("ADB_DEVICES_FAILED: " + result.stderr.strip()[:200])
    assigned: dict[str, str] = {}
    for line in result.stdout.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 2 or fields[1] != "device":
            continue
        serial = fields[0]
        code = adb("-s", serial, "shell", "getprop", "ro.product.device")
        if code.returncode:
            continue
        codename = code.stdout.strip().lower()
        for name, expected in DEVICES.items():
            if codename == expected:
                if name in assigned:
                    raise RuntimeError("AMBIGUOUS_DEVICE: " + name)
                assigned[name] = serial
    return assigned


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="MeteorMesh ADB single-app maintenance")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Read-only inventory of paired, connected phones")
    p_install = sub.add_parser("install", help="Install the pinned MeteorNode build on one phone")
    p_install.add_argument("device", choices=DEVICES)
    p_install.add_argument("apk", type=Path)
    p_uninstall = sub.add_parser("uninstall", help="Delete MeteorNode on one phone, with confirmation")
    p_uninstall.add_argument("device", choices=DEVICES)
    args = parser.parse_args()

    if not shutil.which("adb"):
        raise RuntimeError("ADB_NOT_INSTALLED: install Android platform-tools first")
    devices = connected()
    if args.command == "status":
        for name in DEVICES:
            if name not in devices:
                print(f"{name}: OFFLINE_OR_NOT_PAIRED")
            else:
                print(f"{name}: CONNECTED ({devices[name]}) MeteorNode={'INSTALLED' if installed(devices[name]) else 'NOT_INSTALLED'}")
        return 0

    serial = devices.get(args.device)
    if not serial:
        raise RuntimeError(f"{args.device}: OFFLINE_OR_NOT_PAIRED (no change)")
    if args.command == "install":
        path = args.apk.expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() != ".apk":
            raise RuntimeError("NOT_AN_APK_FILE")
        actual = digest(path)
        if actual != RELEASE_SHA256:
            raise RuntimeError(f"APK_HASH_MISMATCH: {actual}; no installation attempted")
        print(f"Installing verified MeteorNode 0.13.2 to {args.device} ({serial})")
        result = adb("-s", serial, "install", "-r", str(path), timeout=180)
        print((result.stdout + "\n" + result.stderr).strip()[:800])
        if result.returncode:
            print("Existing signature may be incompatible. Do NOT auto-uninstall.")
        return result.returncode

    print(f"WARNING: uninstalling MeteorNode from {args.device} deletes app-private MP4 files.")
    answer = input(f"Type UNINSTALL {args.device} to proceed: ").strip()
    if answer != "UNINSTALL " + args.device:
        print("CANCELLED; no change")
        return 0
    if not installed(serial):
        print("NOT_INSTALLED; no change")
        return 0
    result = adb("-s", serial, "uninstall", PACKAGE, timeout=120)
    print((result.stdout + "\n" + result.stderr).strip()[:800])
    return result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
