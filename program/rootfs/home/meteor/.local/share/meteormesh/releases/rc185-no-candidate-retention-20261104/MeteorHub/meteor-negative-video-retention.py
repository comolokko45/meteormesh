#!/usr/bin/env python3
"""Remove a verified camera MP4 after a successful zero-candidate analysis.

Only camera-folder MP4s are eligible. Analysis JSON, sidecars, event clips,
acceptance media, failed jobs, and videos with candidates are retained.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys

DATA = Path("/srv/meteor-data")
DB = DATA / "meteorhub.sqlite3"
EXPECTED_UUID = "444a8a6d-090a-456c-8a9d-d99665c32574"
STATE = Path.home() / ".local/state/meteormesh"
ACTIVATION = STATE / "negative-video-retention-enabled.json"
RECEIPTS = STATE / "negative-video-retention.jsonl"
CAMERAS = {"cam-ruby", "cam-surya", "cam-viva"}
# MeteorNode segment IDs contain an uppercase ISO-8601 ``T`` and ``Z``
# (for example ``ruby_20261104T174527Z_1ba99ced0259``). Keep the path-safe
# ASCII allowlist while accepting those legitimate characters.
SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$", re.ASCII)
TIME_RE = re.compile(r"_(\d{8}T\d{6}Z)_")
ADJACENT_SECONDS = 315


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_hdd() -> None:
    if DATA.is_symlink() or not DATA.is_dir() or not os.path.ismount(DATA):
        raise RuntimeError("METEOR_DATA_MOUNT_UNAVAILABLE")
    result = subprocess.run(
        ["findmnt", "-n", "-o", "UUID", "--mountpoint", str(DATA)],
        check=False, capture_output=True, text=True, timeout=10,
    )
    if result.returncode or result.stdout.strip() != EXPECTED_UUID:
        raise RuntimeError("METEOR_DATA_UUID_MISMATCH")
    if DB.is_symlink() or not DB.is_file() or DB.stat().st_dev != DATA.stat().st_dev:
        raise RuntimeError("METEORHUB_SQLITE_NOT_ON_VERIFIED_HDD")


def activation_time() -> str:
    if ACTIVATION.is_symlink() or not ACTIVATION.is_file():
        raise RuntimeError("RETENTION_ACTIVATION_MARKER_MISSING")
    doc = json.loads(ACTIVATION.read_text(encoding="utf-8"))
    value = doc.get("activation_utc")
    if not isinstance(value, str) or not value.endswith("+00:00"):
        raise RuntimeError("RETENTION_ACTIVATION_MARKER_INVALID")
    dt.datetime.fromisoformat(value)
    return value


def safe_result_path(value: str, device: str, segment: str) -> Path:
    path = Path(value)
    expected_parent = DATA / "detector-results" / device
    if path.is_symlink() or not path.is_file() or expected_parent.is_symlink():
        raise RuntimeError("ANALYSIS_RESULT_MISSING_OR_UNSAFE")
    if (path.parent != expected_parent or path.stat().st_dev != DATA.stat().st_dev or
            path.suffix != ".json" or not path.name.startswith(segment + "_")):
        raise RuntimeError("ANALYSIS_RESULT_OUTSIDE_HDD")
    return path


def read_result(row: sqlite3.Row) -> dict:
    if row["detector_input_sha256"] != row["video_sha256"]:
        raise RuntimeError("ANALYSIS_INPUT_SHA256_MISMATCH")
    path = safe_result_path(row["result_path"], row["device_id"], row["segment_id"])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row["result_sha256"]:
        raise RuntimeError("ANALYSIS_RESULT_SHA256_MISMATCH")
    result = json.loads(raw)
    if not isinstance(result, dict) or not isinstance(result.get("version"), str):
        raise RuntimeError("ANALYSIS_RESULT_SCHEMA_INVALID")
    if not isinstance(result.get("results"), list):
        raise RuntimeError("ANALYSIS_RESULT_CANDIDATE_LIST_INVALID")
    return result


def segment_epoch(segment_id: str) -> float | None:
    match = TIME_RE.search(segment_id)
    if not match:
        return None
    try:
        return dt.datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=dt.timezone.utc
        ).timestamp()
    except ValueError:
        return None


def candidate_times(db: sqlite3.Connection) -> dict[str, list[float]]:
    found: dict[str, list[float]] = {camera: [] for camera in CAMERAS}
    rows = db.execute(
        "SELECT j.device_id,j.segment_id,j.video_sha256,d.input_sha256 AS detector_input_sha256,"
        "d.result_path,d.result_sha256 "
        "FROM jobs j JOIN detector_runs d ON d.job_id=j.id "
        "WHERE j.state='SUCCEEDED' ORDER BY j.id"
    )
    for row in rows:
        device, segment = row["device_id"], row["segment_id"]
        if device not in CAMERAS:
            continue
        try:
            doc = read_result(row)
        except (OSError, ValueError, RuntimeError):
            continue
        when = segment_epoch(segment)
        if doc["results"] and when is not None:
            found[device].append(when)
    return found


def ensure_table(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS source_dispositions (
        device_id TEXT NOT NULL,
        segment_id TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        disposition TEXT NOT NULL CHECK(disposition IN (
            'DELETE_PENDING','NO_CANDIDATE_DELETED',
            'CANDIDATE_PRESERVED','ADJACENT_CANDIDATE_PRESERVED')),
        analysis_job_id INTEGER NOT NULL,
        result_sha256 TEXT NOT NULL,
        updated_utc TEXT NOT NULL,
        deleted_utc TEXT,
        PRIMARY KEY(device_id,segment_id)
    )""")
    db.commit()


def open_db(read_only: bool) -> sqlite3.Connection:
    uri = DB.as_uri() + ("?mode=ro" if read_only else "")
    connection = sqlite3.connect(uri, uri=read_only, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    if read_only:
        connection.execute("PRAGMA query_only=ON")
    return connection


def source_paths(device: str, segment: str) -> tuple[Path, Path]:
    if device not in CAMERAS or not SEGMENT_RE.fullmatch(segment):
        raise RuntimeError("CAMERA_SOURCE_ID_INVALID")
    folder = DATA / device
    if folder.is_symlink() or not folder.is_dir():
        raise RuntimeError("CAMERA_FOLDER_MISSING_OR_UNSAFE")
    video = folder / (segment + ".mp4")
    sidecar = folder / (segment + ".json")
    if video.is_symlink() or sidecar.is_symlink():
        raise RuntimeError("CAMERA_SOURCE_IS_SYMLINK")
    return video, sidecar


def append_receipt(doc: dict) -> None:
    if STATE.is_symlink():
        raise RuntimeError("RETENTION_STATE_DIRECTORY_SYMLINK")
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if STATE.stat().st_uid != os.getuid() or STATE.stat().st_mode & 0o077:
        raise RuntimeError("RETENTION_STATE_DIRECTORY_PERMISSIONS_UNSAFE")
    fd = os.open(RECEIPTS, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        payload = (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def disposition_row(db: sqlite3.Connection, device: str, segment: str):
    return db.execute(
        "SELECT sha256,disposition FROM source_dispositions WHERE device_id=? AND segment_id=?",
        (device, segment),
    ).fetchone()


def write_disposition(db: sqlite3.Connection, row: sqlite3.Row,
                      disposition: str, deleted_utc: str | None = None) -> None:
    db.execute("""INSERT INTO source_dispositions
        (device_id,segment_id,sha256,disposition,analysis_job_id,result_sha256,updated_utc,deleted_utc)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(device_id,segment_id) DO UPDATE SET
        sha256=excluded.sha256, disposition=excluded.disposition,
        analysis_job_id=excluded.analysis_job_id,
        result_sha256=excluded.result_sha256,
        updated_utc=excluded.updated_utc, deleted_utc=excluded.deleted_utc""",
        (row["device_id"], row["segment_id"], row["video_sha256"], disposition,
         row["job_id"], row["result_sha256"], now_utc(), deleted_utc))
    db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report eligible clips without deleting")
    args = parser.parse_args()
    verify_hdd()
    enabled_after = activation_time()
    lock_path = DATA / ".meteor-morning.lock"
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RuntimeError("METEOR_ANALYSIS_LOCK_MISSING")
    with lock_path.open("r+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("NEGATIVE_RETENTION: SKIPPED_ANALYSIS_OR_SYNC_LOCKED")
            return 0

        db = open_db(read_only=args.dry_run)
        try:
            has_table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_dispositions'"
            ).fetchone() is not None
            if not has_table and not args.dry_run:
                ensure_table(db)
                has_table = True
            candidates = candidate_times(db)
            disposition_join = (
                "LEFT JOIN source_dispositions sd ON sd.device_id=j.device_id AND sd.segment_id=j.segment_id"
                if has_table else ""
            )
            disposition_select = (
                "sd.sha256 AS disposition_sha256,sd.disposition AS disposition"
                if has_table else "NULL AS disposition_sha256,NULL AS disposition"
            )
            rows = db.execute(
                "SELECT j.id AS job_id,j.device_id,j.segment_id,j.video_sha256,j.finished_utc,"
                "d.input_sha256 AS detector_input_sha256,d.result_path,d.result_sha256,"
                "s.sha256 AS segment_sha256,s.size_bytes,s.state AS segment_state,"
                + disposition_select +
                " FROM jobs j JOIN detector_runs d ON d.job_id=j.id "
                "LEFT JOIN segments s ON s.device_id=j.device_id AND s.segment_id=j.segment_id "
                + disposition_join +
                " WHERE j.state='SUCCEEDED' AND j.finished_utc>=? "
                "ORDER BY j.finished_utc,j.id",
                (enabled_after,),
            ).fetchall()

            counts = {"eligible": 0, "deleted": 0, "candidate_preserved": 0,
                      "adjacent_preserved": 0, "skipped": 0}
            for row in rows:
                device, segment = row["device_id"], row["segment_id"]
                if device not in CAMERAS:
                    continue
                existing = row["disposition"]
                if existing in {"NO_CANDIDATE_DELETED", "CANDIDATE_PRESERVED",
                                "ADJACENT_CANDIDATE_PRESERVED"}:
                    continue
                if existing == "DELETE_PENDING" and row["disposition_sha256"] != row["video_sha256"]:
                    counts["skipped"] += 1
                    print(f"RETENTION_SKIP {device}/{segment}: pending SHA changed")
                    continue
                if row["segment_state"] != "INGEST_VERIFIED" or row["segment_sha256"] != row["video_sha256"]:
                    counts["skipped"] += 1
                    print(f"RETENTION_SKIP {device}/{segment}: ingest identity not verified")
                    continue
                try:
                    result = read_result(row)
                except (OSError, ValueError, RuntimeError) as exc:
                    counts["skipped"] += 1
                    print(f"RETENTION_SKIP {device}/{segment}: {type(exc).__name__}:{exc}")
                    continue

                if result["results"]:
                    if not args.dry_run and existing is None:
                        write_disposition(db, row, "CANDIDATE_PRESERVED")
                    counts["candidate_preserved"] += 1
                    continue

                when = segment_epoch(segment)
                near_candidate = when is not None and any(
                    abs(when - candidate_when) <= ADJACENT_SECONDS
                    for candidate_when in candidates.get(device, ())
                )
                if near_candidate:
                    if not args.dry_run and existing is None:
                        write_disposition(db, row, "ADJACENT_CANDIDATE_PRESERVED")
                    counts["adjacent_preserved"] += 1
                    continue

                try:
                    video, sidecar = source_paths(device, segment)
                    if not sidecar.is_file() or sidecar.stat().st_dev != DATA.stat().st_dev:
                        raise RuntimeError("SIDECAR_MISSING_OR_UNSAFE")
                    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
                    if (metadata.get("segment_id") != segment or
                            metadata.get("device_id") != device.removeprefix("cam-") or
                            metadata.get("sha256") != row["video_sha256"] or
                            metadata.get("size_bytes") != row["size_bytes"]):
                        raise RuntimeError("SIDECAR_IDENTITY_MISMATCH")
                    if existing == "DELETE_PENDING" and not video.exists():
                        deleted_utc = now_utc()
                        if not args.dry_run:
                            write_disposition(db, row, "NO_CANDIDATE_DELETED", deleted_utc)
                        counts["deleted"] += 1
                        continue
                    if not video.is_file() or video.stat().st_dev != DATA.stat().st_dev:
                        raise RuntimeError("MP4_MISSING_OR_UNSAFE")
                    if video.stat().st_size != row["size_bytes"]:
                        raise RuntimeError("MP4_SIZE_MISMATCH")
                    if sha256_file(video) != row["video_sha256"]:
                        raise RuntimeError("MP4_SHA256_MISMATCH")
                except (OSError, ValueError, RuntimeError) as exc:
                    counts["skipped"] += 1
                    print(f"RETENTION_SKIP {device}/{segment}: {type(exc).__name__}:{exc}")
                    continue

                counts["eligible"] += 1
                if args.dry_run:
                    print(f"RETENTION_DRY_RUN_DELETE {device}/{segment} bytes={row['size_bytes']}")
                    continue

                if existing is None:
                    write_disposition(db, row, "DELETE_PENDING")
                video.unlink()
                directory_fd = os.open(video.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                deleted_utc = now_utc()
                write_disposition(db, row, "NO_CANDIDATE_DELETED", deleted_utc)
                append_receipt({"schema": "meteormesh.negative-retention.v1",
                                "disposition": "NO_CANDIDATE_DELETED",
                                "device_id": device, "segment_id": segment,
                                "sha256": row["video_sha256"], "bytes": row["size_bytes"],
                                "analysis_job_id": row["job_id"],
                                "result_sha256": row["result_sha256"],
                                "deleted_utc": deleted_utc})
                counts["deleted"] += 1
                print(f"RETENTION_DELETED {device}/{segment} bytes={row['size_bytes']}")
            print("NEGATIVE_RETENTION: " + json.dumps(
                {"mode": "DRY_RUN" if args.dry_run else "APPLY", **counts}, sort_keys=True))
            return 0
        finally:
            db.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"NEGATIVE_RETENTION: ERROR {type(exc).__name__}:{str(exc)[:200]}", file=sys.stderr)
        raise SystemExit(2)
