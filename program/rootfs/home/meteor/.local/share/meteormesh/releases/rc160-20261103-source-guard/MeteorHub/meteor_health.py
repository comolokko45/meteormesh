#!/usr/bin/env python3
"""Read-only, conservative health statistics for the local MeteorHub inventory.

Historical inventory is not an individual capture session. No frame-gap,
night-closure, meteor classification, or retention claim is made here.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path

from meteornode_pull import VALID_ID


def _duration(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        doc = json.loads(raw)
        number = float(doc["format"]["duration"])
        return number if math.isfinite(number) and 0 < number <= 48 * 3600 else None
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return None


def summarize_health(db: sqlite3.Connection, device_ids: list[str]) -> dict:
    if len(device_ids) != len(set(device_ids)) or any(
            not isinstance(name, str) or not VALID_ID.fullmatch(name) for name in device_ids):
        raise ValueError("Invalid or duplicate device identifiers")

    devices = {name: {
        "segments_in_inventory": 0,
        "segments_ingest_verified": 0,
        "total_verified_bytes": 0,
        "verified_video_duration_seconds": 0.0,
        "verified_segments_missing_duration": 0,
        "nonverified_segments": 0,
        "detector_latest_run_count": 0,
        "detector_processing_ratio_R": None,
        "frame_pts_gap_count": "UNVERIFIED",
        "sensor_to_utc_uncertainty_ms": "UNVERIFIED",
    } for name in device_ids}

    durations = {}
    for camera, segment, size, state, probe in db.execute(
            "SELECT device_id, segment_id, size_bytes, state, ffprobe_json FROM segments"):
        if camera not in devices:
            continue
        row = devices[camera]
        row["segments_in_inventory"] += 1
        if state != "INGEST_VERIFIED":
            row["nonverified_segments"] += 1
            continue
        row["segments_ingest_verified"] += 1
        row["total_verified_bytes"] += size
        duration = _duration(probe)
        if duration is None:
            row["verified_segments_missing_duration"] += 1
            continue
        durations[(camera, segment)] = duration
        row["verified_video_duration_seconds"] += duration

    # Older databases can lack the detector_runs table. Missing measurement != zero.
    has_runs = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='detector_runs'").fetchone()
    per_device = {camera: [0.0, 0.0] for camera in device_ids}
    if has_runs:
        runs = db.execute("""SELECT r.device_id, r.segment_id, r.duration_seconds
                             FROM detector_runs r
                             JOIN (SELECT device_id, segment_id, MAX(job_id) AS recent
                                   FROM detector_runs GROUP BY device_id, segment_id) latest
                               ON r.job_id=latest.recent""")
        for camera, segment, run_seconds in runs:
            if camera not in devices:
                continue
            devices[camera]["detector_latest_run_count"] += 1
            video_seconds = durations.get((camera, segment))
            if video_seconds and isinstance(run_seconds, (int, float)) and math.isfinite(run_seconds) and run_seconds >= 0:
                per_device[camera][0] += float(run_seconds)
                per_device[camera][1] += video_seconds

    for camera, values in per_device.items():
        elapsed, video_time = values
        if video_time:
            devices[camera]["detector_processing_ratio_R"] = round(elapsed / video_time, 3)
        devices[camera]["verified_video_duration_seconds"] = round(
            devices[camera]["verified_video_duration_seconds"], 3)

    return {
        "schema": "meteormesh.health.v0.7",
        "scope": "ALL_SESSIONS_IN_LOCAL_DATABASE_NOT_A_CLOSED_NIGHT",
        "capture_session_complete": False,
        "metadata_gap_test_complete": False,
        "science_time_synchronized": False,
        "retention_permission": False,
        "device_health": devices,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--devices", default="cam-ruby,cam-viva,cam-surya")
    args = parser.parse_args()
    db_path = args.data_dir / "meteorhub.sqlite3"
    if args.data_dir.is_symlink() or db_path.is_symlink() or not db_path.is_file():
        raise ValueError("Unsafe or absent MeteorHub database")
    # File mode=ro; SQLite cannot initialize/create missing DB or modify schema.
    with sqlite3.connect(db_path.resolve(strict=True).as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        print(json.dumps(summarize_health(db, args.devices.split(",")), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
