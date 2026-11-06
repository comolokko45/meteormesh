#!/usr/bin/env python3
"""Tiny append-only human annotation log for provisional meteor candidates.

Labels are educational/review metadata. They are NEVER archive or purge orders.
No web server, extra database, or external service is required.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path

from meteornode_pull import VALID_ID


LABELS = ("meteor_possible", "aircraft_satellite", "artifact", "uncertain")
CANDIDATE_ID = re.compile(r"^[0-9a-f]{20}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


from mm01_bridge import guarded_subprocess_output as _mm01_process_output, guarded_db_connect as _mm01_db, verify_write_path as _mm01_verify, is_unsafe_symlink as _mm01_unsafe_link, guarded_partial_open as _mm01_open_partial, guarded_output_dir as _mm01_output_dir, guarded_detector_run as _mm01_detector_run, guarded_publish as _mm01_publish, is_protected_write_context as _mm01_active
def source_from_report(report: Path, candidate_id: str) -> dict:
    if not CANDIDATE_ID.fullmatch(candidate_id):
        raise ValueError("Candidate ID must be 20 lowercase hexadecimal characters")
    if not report.is_file() or report.is_symlink() or report.stat().st_size > 20 * 1024**2:
        raise ValueError("Unsafe or oversized candidate report")
    payload = report.read_bytes()
    data = json.loads(payload)
    if (not isinstance(data, dict) or
            data.get("schema") != "meteormesh.event.provisional.v0.2" or
            data.get("auto_delete") is not False or
            not isinstance(data.get("events"), list)):
        raise ValueError("Not a MeteorMesh event report")
    candidates = [c for event in data["events"] if isinstance(event, dict)
                  for c in event.get("candidates", []) if isinstance(c, dict)
                  and c.get("candidate_id") == candidate_id]
    if len(candidates) != 1:
        raise ValueError("Candidate missing or duplicated in this report")
    record = candidates[0]
    device, segment, digest = (record.get("device_id"), record.get("segment_id"),
                               record.get("source_sha256"))
    if not isinstance(device, str) or not VALID_ID.fullmatch(device) or (
            not isinstance(segment, str) or not VALID_ID.fullmatch(segment)) or (
            not isinstance(digest, str) or not SHA256.fullmatch(digest)):
        raise ValueError("Candidate source provenance missing")
    return {"candidate_id": candidate_id, "device_id": device,
            "segment_id": segment, "source_sha256": digest,
            "event_report_sha256": hashlib.sha256(payload).hexdigest()}


def append_review(data_dir: Path, event_report: Path, candidate_id: str,
                  label: str, note: str = "") -> dict:
    _mm01_verify(data_dir)
    if label not in LABELS:
        raise ValueError("Unknown review label")
    if not isinstance(note, str) or len(note) > 280 or "\x00" in note:
        raise ValueError("Note too long or invalid")
    source = source_from_report(event_report, candidate_id)
    if _mm01_unsafe_link(data_dir) or not data_dir.is_dir():
        raise ValueError("Existing non-symlink data directory required")
    log = data_dir / "candidate-review.jsonl"
    if log.is_symlink() or (log.exists() and not log.is_file()):
        raise ValueError("Review file path is unsafe")
    record = {**source, "label": label, "note": note,
              "reviewed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "retention_permission": False, "scientific_confirmation": False}
    data = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    # Append-only: correcting a label adds a new review, never erases history.
    _mm01_verify(log)
    fd = os.open(log, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "ab") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return record


def read_reviews(data_dir: Path, candidate_id: str | None = None) -> list[dict]:
    if candidate_id is not None and not CANDIDATE_ID.fullmatch(candidate_id):
        raise ValueError("Invalid candidate ID")
    log = data_dir / "candidate-review.jsonl"
    if _mm01_unsafe_link(data_dir) or log.is_symlink():
        raise ValueError("Unsafe review path")
    if not log.exists():
        return []
    entries = []
    with log.open(encoding="utf-8") as stream:
        for line in stream:
            entry = json.loads(line)
            if candidate_id is None or entry.get("candidate_id") == candidate_id:
                entries.append(entry)
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    add = commands.add_parser("label")
    add.add_argument("--data-dir", type=Path, required=True)
    add.add_argument("--event-report", type=Path, required=True)
    add.add_argument("--candidate-id", required=True)
    add.add_argument("--label", choices=LABELS, required=True)
    add.add_argument("--note", default="")
    listing = commands.add_parser("list")
    listing.add_argument("--data-dir", type=Path, required=True)
    listing.add_argument("--candidate-id")
    args = parser.parse_args()
    # MM01_GUARDED_CLI_V0_4
    from contextlib import nullcontext as _mm01_nullcontext
    from mm01_bridge import guarded_cli as _mm01_guarded_cli
    _mm01_mode = ('review-label' if args.action == 'label' else None)
    with (_mm01_guarded_cli(args, mode=_mm01_mode) if _mm01_mode else _mm01_nullcontext()):
        if args.action == "label":
            result = append_review(args.data_dir, args.event_report, args.candidate_id,
                                   args.label, args.note)
        else:
            result = read_reviews(args.data_dir, args.candidate_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
