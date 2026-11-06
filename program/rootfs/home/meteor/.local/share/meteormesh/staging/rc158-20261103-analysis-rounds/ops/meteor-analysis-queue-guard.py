#!/usr/bin/python3
"""Fail-closed, read-only guard for scoped MeteorMesh analysis batches."""
from __future__ import annotations
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(os.environ.get("METEORMESH_DATA_DIR", "/srv/meteor-data"))
DB = ROOT / "meteorhub.sqlite3"
SCOPE = Path(os.environ.get(
    "METEORMESH_NIGHT_SCOPE",
    str(Path.home() / ".local/share/meteormesh/staging/fullnight/night-scope-current.json"),
))
CONFIG = Path(os.environ.get(
    "METEORMESH_DEVICE_CONFIG",
    str(Path.home() / ".config/meteormesh/devices-three-lab-rc12.json"),
))
HUB = Path(os.environ.get("METEORMESH_HUB_PATH", ""))


def main() -> int:
    if not os.path.ismount(ROOT) or ROOT.is_symlink():
        raise RuntimeError("DATA_MOUNT_NOT_VERIFIED")
    for path in (DB, SCOPE, CONFIG):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("REQUIRED_PATH_MISSING_OR_UNSAFE:" + path.name)
    if HUB.is_symlink() or not HUB.is_dir():
        raise RuntimeError("HUB_SOURCE_MISSING_OR_UNSAFE")
    sys.path.insert(0, str(HUB))
    from night_scope import load_night_scope

    scope = load_night_scope(SCOPE)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    configured = {x.get("id") for x in config.get("devices", []) if isinstance(x, dict)}
    required = set(scope.excluded)
    if configured != required:
        raise RuntimeError("SCOPE_DEVICE_CONFIG_MISMATCH")
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT device_id,segment_id,video_sha256,state FROM jobs "
            "WHERE state IN ('WAITING','RUNNING')"
        )
        counts = {device: 0 for device in sorted(required)}
        total = 0
        for device, segment_id, digest, _state in rows:
            if scope.allows_queue(device, segment_id, digest):
                counts[device] = counts.get(device, 0) + 1
                total += 1
    finally:
        connection.close()
    if total:
        print(f"ANALYZE_QUEUE_READY scope={scope.night_id} eligible={total} devices={counts}")
        return 0
    print(f"ANALYZE_QUEUE_EMPTY scope={scope.night_id}")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ANALYZE_QUEUE_GUARD_ERROR {type(exc).__name__}:{exc}", file=sys.stderr)
        raise SystemExit(255)
