#!/usr/bin/env python3
"""MeteorMesh MeteorHub 0.1: inventory and safe, sequential morning ingest.

Python standard library only. No video detection, remote command execution,
upload, retention receipt, or deletion capability exists in this version.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Callable

from meteornode_pull import (MeteorNode, MeteorNodeLabHTTP, VALID_ID,
                             normalize_fingerprint, require_lab_endpoint)


DEVICE_ID = re.compile(r"^[a-zA-Z0-9_-]{3,60}$")
TOKEN_ENV = re.compile(r"^[A-Z_][A-Z0-9_]*$")


from mm01_bridge import guarded_db_connect as _mm01_db, verify_write_path as _mm01_verify, is_unsafe_symlink as _mm01_unsafe_link, guarded_partial_open as _mm01_open_partial, guarded_output_dir as _mm01_output_dir, guarded_detector_run as _mm01_detector_run, guarded_publish as _mm01_publish, is_protected_write_context as _mm01_active, guarded_ffprobe_run as _mm01_ffprobe_run
def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_device_config(path: Path) -> list[dict]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Configuration must contain a nonempty devices array")
    devices = config.get("devices")
    if not isinstance(devices, list) or not devices:
        raise ValueError("Configuration must contain a nonempty devices array")
    result = []
    identifiers = set()
    for item in devices:
        if not isinstance(item, dict):
            raise ValueError("Invalid device entry")
        name = item.get("id")
        if not isinstance(name, str) or not DEVICE_ID.fullmatch(name):
            raise ValueError(f"Unsafe or missing device id: {name!r}")
        if name in identifiers:
            raise ValueError(f"Duplicate device id: {name}")
        identifiers.add(name)
        if not item.get("enabled", True):
            continue
        host = item.get("host")
        # The configured host is used in HTTPS and HTTP Host framing. No
        # controls, URI punctuation or embedded ports may enter this field.
        if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host):
            raise ValueError(f"Invalid host for {name}")
        port = item.get("port", 8443)
        if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
            raise ValueError(f"Invalid port for {name}")
        transport = item.get("transport", "tls_pinned")
        if transport == "tls_pinned":
            value = item.get("fingerprint", "")
            if not isinstance(value, str):
                raise ValueError("Invalid fingerprint")
            fingerprint = normalize_fingerprint(value)
        elif transport == "http_lab":
            require_lab_endpoint(host, port)
            if item.get("fingerprint") not in (None, ""):
                raise ValueError("HTTP lab must not supply TLS fingerprint")
            fingerprint = None
        else:
            raise ValueError(f"Unsupported transport: {transport!r}")
        token_env = item.get("token_env")
        if not isinstance(token_env, str) or not TOKEN_ENV.fullmatch(token_env):
            raise ValueError(f"Invalid token_env for {name}")
        config_row = {"id": name, "host": host, "port": port,
                      "fingerprint": fingerprint, "token_env": token_env}
        if transport == "http_lab":
            config_row["transport"] = "http_lab"
        result.append(config_row)
    return result


def get_inventory() -> dict:
    commands = {
        "lscpu": ["lscpu"],
        "memory": ["free", "-h"],
        "block_devices": ["lsblk", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS"],
        "disk_free": ["df", "-h"],
        "kernel": ["uname", "-a"],
    }
    details = {}
    for name, argv in commands.items():
        try:
            run = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
            details[name] = {"exit_code": run.returncode, "stdout": run.stdout[:20000],
                             "stderr": run.stderr[:1000]}
        except (OSError, subprocess.TimeoutExpired) as exc:
            details[name] = {"error": str(exc)}
    return {"schema": 1, "generated_utc": utc_now(), "os": platform.platform(),
            "python": platform.python_version(), "commands": details}


def create_database(path: Path) -> sqlite3.Connection:
    _mm01_verify(path)
    db = _mm01_db(path, create=True)
    db.execute("""CREATE TABLE IF NOT EXISTS segments (
        device_id TEXT NOT NULL, segment_id TEXT NOT NULL, sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL, state TEXT NOT NULL, ffprobe_json TEXT,
        verified_utc TEXT NOT NULL, PRIMARY KEY (device_id, segment_id)
    )""")
    db.commit()
    return db


def verify_with_ffprobe(video: Path) -> tuple[str, dict | None]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return "HASH_VERIFIED_FFPROBE_MISSING", None
    command = [ffprobe, "-v", "error", "-show_entries",
               "format=duration,size:stream=codec_type,codec_name,width,height",
               "-of", "json", str(video)]
    try:
        completed = _mm01_ffprobe_run(subprocess.run, command, video, timeout=45)
        if completed.returncode:
            return "FFPROBE_FAILED", {"stderr": completed.stderr[:1000]}
        info = json.loads(completed.stdout)
        streams = info.get("streams", [])
        duration = float(info.get("format", {}).get("duration", 0))
        if not isinstance(streams, list):
            return "FFPROBE_FAILED", info
        valid_video = any(s.get("codec_type") == "video" and
                          type(s.get("width")) is int and 0 < s["width"] <= 16384 and
                          type(s.get("height")) is int and 0 < s["height"] <= 16384
                          for s in streams if isinstance(s, dict))
        if not math.isfinite(duration) or duration <= 0 or not valid_video:
            return "FFPROBE_FAILED", info
        return "INGEST_VERIFIED", info
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError,
            AttributeError) as exc:
        return "FFPROBE_FAILED", {"error": str(exc)}


def store_verified_segment(db: sqlite3.Connection, device: str, segment: dict,
                           state: str, probe: dict | None) -> None:
    segment_id = segment["segment_id"]
    sha = str(segment["sha256"]).lower()
    size = int(segment["size_bytes"])
    old = db.execute("SELECT sha256, size_bytes FROM segments WHERE device_id=? AND segment_id=?",
                     (device, segment_id)).fetchone()
    if old is not None and (old[0] != sha or old[1] != size):
        raise RuntimeError(f"Segment identity changed; refusing DB overwrite: {device}/{segment_id}")
    db.execute("""INSERT INTO segments
        (device_id, segment_id, sha256, size_bytes, state, ffprobe_json, verified_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_id, segment_id) DO UPDATE SET
        state=excluded.state, ffprobe_json=excluded.ffprobe_json,
        verified_utc=excluded.verified_utc""",
        (device, segment_id, sha, size, state,
         json.dumps(probe, ensure_ascii=False) if probe is not None else None, utc_now()))
    db.commit()


def make_node(device: dict) -> MeteorNode:
    token = os.environ.get(device["token_env"], "")
    if not token:
        raise ValueError(f"Missing pairing environment variable: {device['token_env']}")
    if device.get("transport", "tls_pinned") == "http_lab":
        return MeteorNodeLabHTTP(device["host"], device["port"], token)
    return MeteorNode(device["host"], device["port"], device["fingerprint"], token)


def safely_prepare_folder(root: Path, device_id: str) -> Path:
    _mm01_verify(root)
    if _mm01_unsafe_link(root):
        raise RuntimeError(f"Data root is a symlink: {root}")
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise RuntimeError(f"Not a directory: {root}")
    folder = root / device_id
    if folder.is_symlink():
        raise RuntimeError(f"Device folder is a symlink: {folder}")
    folder = _mm01_output_dir(folder)
    return folder


def process_device(device: dict, data_root: Path, db: sqlite3.Connection | None,
                   list_only: bool, min_free_bytes: int = 0,
                   should_stop: Callable[[], bool] | None = None,
                   max_segments: int | None = None, scope=None) -> tuple[int, int]:
    _mm01_verify(data_root)
    name = device["id"]
    node = make_node(device)
    health = node.get_json("/api/v1/health")
    inventory = node.get_json("/api/v1/segments")
    segments = inventory.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError(f"Invalid segments list from {name}")
    print(f"{name}: {health.get('state', '?')}, {len(segments)} ready, "
          f"{health.get('free_bytes', '?')} free bytes")
    if list_only:
        return 0, 0
    assert db is not None
    if scope is not None:
        # Filter before folder creation, disk writes, or transfer; never count
        # deliberately excluded test recordings as incomplete night segments.
        segments = [s for s in segments if scope.allows(name, s.get("segment_id"), str(s.get("sha256", "")).lower())]
    folder = safely_prepare_folder(data_root, name)
    good, pending = 0, 0
    if max_segments is not None:
        if not 1 <= max_segments <= 100:
            raise ValueError("max_segments must be between 1 and 100")
        # A deliberately capped acceptance run resumes an existing .part first;
        # never re-download a different video while a recoverable partial exists.
        if len(segments) > max_segments:
            parts = {p.name[:-len('.mp4.part')] for p in folder.glob('*.mp4.part')
                     if p.is_file() and not p.is_symlink()}
            completed = {p.name[:-len('.mp4')] for p in folder.glob('*.mp4')
                         if p.is_file() and not p.is_symlink()}
            def _resume_priority(item):
                segment_id = str(item.get('segment_id', ''))
                return (0 if segment_id in parts else
                        1 if segment_id in completed else 2, segment_id)
            segments = sorted(segments, key=_resume_priority)
            skipped = len(segments) - max_segments
            segments = segments[:max_segments]
            pending += skipped
            print(f'  {name}: {skipped} READY segment(s) deferred by explicit limit')
    for index, segment in enumerate(segments):
        if should_stop is not None and should_stop():
            pending += len(segments) - index
            print(f"  {name}: budget expired; {len(segments) - index} segments left on phone")
            break
        try:
            segment_id = segment["segment_id"]
            if not isinstance(segment_id, str) or not VALID_ID.fullmatch(segment_id):
                raise ValueError("Invalid segment ID")
            if min_free_bytes:
                node.pull_one(segment, folder, min_free_bytes=min_free_bytes)
            else:
                node.pull_one(segment, folder)
            # Defense in depth: a verified video without its verified metadata
            # must NEVER enter the analysis queue, even after an interrupted sync.
            sidecar = folder / f"{segment_id}.json"
            if sidecar.is_symlink() or not sidecar.is_file():
                raise RuntimeError("Ingest sidecar missing or unsafe")
            MeteorNode.verify_metadata(sidecar, segment_id,
                                       int(segment["size_bytes"]), str(segment["sha256"]).lower())
            state, probe = verify_with_ffprobe(folder / f"{segment_id}.mp4")
            store_verified_segment(db, name, segment, state, probe)
            if state == "INGEST_VERIFIED":
                good += 1
            else:
                pending += 1
            print(f"  {segment_id}: {state}")
        except Exception as exc:
            pending += 1
            print(f"  {segment.get('segment_id', '?')}: ERROR {exc}", file=sys.stderr)
    return good, pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inventory_cmd = sub.add_parser("inventory", help="Inspect Linux host hardware")
    inventory_cmd.add_argument("--output", type=Path,
                               help="Optional new JSON file (refuses to overwrite an existing file)")
    for name in ("check", "sync"):
        p = sub.add_parser(name, help="Read device status or download all completed MP4")
        p.add_argument("--config", type=Path, required=True)
        if name == "sync":
            p.add_argument("--data-dir", type=Path, required=True)
            p.add_argument("--max-segments", type=int, default=None,
                           help="Manual acceptance only; resume partial files first")
    args = parser.parse_args(argv)
    # MM01_GUARDED_CLI_V0_4
    from contextlib import nullcontext as _mm01_nullcontext
    from mm01_bridge import guarded_cli as _mm01_guarded_cli
    _mm01_mode = ('hub-sync' if args.command == 'sync' else 'inventory-output' if args.command == 'inventory' and args.output else None)
    with (_mm01_guarded_cli(args, mode=_mm01_mode) if _mm01_mode else _mm01_nullcontext()):

        if args.command == "inventory":
            content = json.dumps(get_inventory(), ensure_ascii=False, indent=2)
            if args.output:
                with args.output.open("x", encoding="utf-8") as handle:
                    handle.write(content + "\n")
                print(f"Inventory saved: {args.output}")
            else:
                print(content)
            return 0

        devices = read_device_config(args.config)
        if not devices:
            print("No enabled devices")
            return 0
        db = None
        if args.command == "sync":
            root = args.data_dir
            if _mm01_unsafe_link(root):
                raise RuntimeError("Data directory must not be a symlink")
            root.mkdir(parents=True, exist_ok=True)
            db = create_database(root / "meteorhub.sqlite3")
        else:
            root = Path(".")

        failures = 0
        try:
            for device in devices:
                try:
                    kwargs = ({'max_segments': args.max_segments}
                              if args.command == 'sync' and args.max_segments is not None
                              else {})
                    _, pending = process_device(device, root, db,
                                                args.command == "check", **kwargs)
                    failures += pending
                except Exception as exc:
                    failures += 1
                    print(f"{device['id']}: ERROR {exc}", file=sys.stderr)
        finally:
            if db is not None:
                db.close()
        return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"MeteorHub configuration/system error: {exc}", file=sys.stderr)
        sys.exit(2)
