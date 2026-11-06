#!/usr/bin/python3
"""Read-only systemd guard: skip analysis rounds when the scoped queue has no MP4 source."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

CAMERAS = {"cam-ruby", "cam-surya", "cam-viva"}
SEGMENT = re.compile(r"^[A-Za-z0-9_-]+_\d{8}T\d{6}Z_[0-9a-f]{12}$")
RETIRED = {"DELETE_PENDING", "NO_CANDIDATE_DELETED"}


def source_ready(data_root: Path, device: str, segment_id: str) -> bool:
    if device not in CAMERAS or not isinstance(segment_id, str) or not SEGMENT.fullmatch(segment_id):
        return False
    folder = data_root / device
    video = folder / (segment_id + ".mp4")
    if folder.is_symlink() or not folder.is_dir() or video.is_symlink() or not video.is_file():
        return False
    try:
        root_real = data_root.resolve(strict=True)
        folder_real = folder.resolve(strict=True)
        video_real = video.resolve(strict=True)
        return (
            folder_real.parent == root_real
            and video_real.parent == folder_real
            and video.stat().st_size > 0
        )
    except (OSError, RuntimeError):
        return False


def evaluate_queue(db: sqlite3.Connection, data_root: Path, scope) -> dict:
    db.execute("PRAGMA query_only=ON")
    rows = db.execute(
        "SELECT device_id,segment_id,video_sha256 FROM jobs WHERE state IN ('WAITING','RUNNING')"
    ).fetchall()
    has_dispositions = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_dispositions'"
    ).fetchone() is not None
    dispositions = {}
    if has_dispositions:
        dispositions = {
            (device, segment): (str(digest).lower(), str(state))
            for device, segment, digest, state in db.execute(
                "SELECT device_id,segment_id,sha256,disposition FROM source_dispositions"
            )
        }

    counts = {
        "night_id": scope.night_id,
        "eligible_jobs": 0,
        "source_ready_jobs": 0,
        "source_missing_jobs": 0,
        "retired_jobs": 0,
        "by_device_ready": {device: 0 for device in sorted(CAMERAS)},
    }
    for device, segment_id, digest in rows:
        if not scope.allows_queue(device, segment_id, digest):
            continue
        counts["eligible_jobs"] += 1
        prior = dispositions.get((device, segment_id))
        if prior is not None and prior[1] in RETIRED:
            if prior[0] != str(digest).lower():
                raise RuntimeError("RETIRED_SOURCE_DIGEST_MISMATCH")
            counts["retired_jobs"] += 1
            continue
        if source_ready(data_root, device, segment_id):
            counts["source_ready_jobs"] += 1
            counts["by_device_ready"][device] += 1
        else:
            counts["source_missing_jobs"] += 1
    return counts


def run(args) -> int:
    root = Path(args.data_dir)
    db_path = root / "meteorhub.sqlite3"
    scope_path = Path(args.night_scope)
    hub = Path(args.hub)
    if root.is_symlink() or not os.path.ismount(root):
        raise RuntimeError("DATA_MOUNT_NOT_VERIFIED")
    if db_path.is_symlink() or not db_path.is_file() or db_path.resolve(strict=True).parent != root.resolve(strict=True):
        raise RuntimeError("DATABASE_PATH_UNSAFE_OR_MISSING")
    if scope_path.is_symlink() or not scope_path.is_file():
        raise RuntimeError("NIGHT_SCOPE_MISSING_OR_UNSAFE")
    if hub.is_symlink() or not hub.is_dir() or not (hub / "night_scope.py").is_file():
        raise RuntimeError("NIGHT_SCOPE_SOURCE_MISSING_OR_UNSAFE")

    sys.path.insert(0, str(hub))
    from night_scope import load_night_scope

    scope = load_night_scope(scope_path)
    uri = db_path.resolve(strict=True).as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        result = evaluate_queue(db, root, scope)
    finally:
        db.close()

    tag = "ANALYSIS_SOURCE_READY" if result["source_ready_jobs"] else "ANALYSIS_QUEUE_NO_ACTIONABLE_SOURCE"
    print(tag + ": " + json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
    return 0 if result["source_ready_jobs"] else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="/srv/meteor-data")
    parser.add_argument("--night-scope", required=True)
    parser.add_argument("--hub", required=True, help="MeteorHub directory containing night_scope.py")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("ANALYSIS_SOURCE_GUARD_ERROR: " + type(exc).__name__ + ":" + str(exc), file=sys.stderr)
        raise SystemExit(255)
