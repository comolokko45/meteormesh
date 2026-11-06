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
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
from zoneinfo import ZoneInfo

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
# Require a nearby successful segment for the target and both neighboring
# five-minute intervals on each camera. A candidate time guard must not
# also serve as proof that a different camera covered the same segment.
COVERAGE_ALIGNMENT_SECONDS = 60
SEGMENT_TARGET_SECONDS = 300
# The old 315-second guard ignored up to 60 seconds of allowed camera start
# misalignment. Keep that uncertainty plus the existing 15-second buffer.
# This is a conservative filter, not proof of absolute UTC accuracy.
ADJACENT_SECONDS = SEGMENT_TARGET_SECONDS + COVERAGE_ALIGNMENT_SECONDS + 15
MAX_APPLY_PER_RUN = 5
MAX_DRY_RUN_CANDIDATES = 5
DEVICE_CONFIG = Path.home() / '.config/meteormesh/devices-three-lab-rc12.json'
PRIVATE_LANS = tuple(ipaddress.ip_network(x) for x in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def require_capture_quiet(now: dt.datetime | None = None, *, config_path: Path | None = None,
                          runner=subprocess.run, connection_factory=http.client.HTTPConnection) -> None:
    """Fail closed before deletion if capture window, clock or any camera is uncertain."""
    instant = now or dt.datetime.now(dt.timezone.utc)
    if instant.tzinfo is None:
        raise RuntimeError('CLEANUP_CLOCK_UNVERIFIED')
    local = instant.astimezone(ZoneInfo('Europe/Istanbul'))
    if not (dt.time(5, 30) <= local.time().replace(tzinfo=None) < dt.time(18, 45)):
        raise RuntimeError('CLEANUP_OUTSIDE_SAFE_DAY_WINDOW')
    try:
        ntp = runner(['/usr/bin/timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
                     capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('CLEANUP_CLOCK_UNVERIFIED') from exc
    if ntp.returncode != 0 or ntp.stdout.strip() != 'yes':
        raise RuntimeError('CLEANUP_CLOCK_UNVERIFIED')
    path = config_path or DEVICE_CONFIG
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('CLEANUP_PHONE_CONFIG_UNAVAILABLE')
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
        devices = config.get('devices') if isinstance(config, dict) else None
        if not isinstance(devices, list):
            raise ValueError('missing devices')
        enabled = [d for d in devices if isinstance(d, dict) and d.get('enabled', True)]
        if len(enabled) != len(CAMERAS) or {d.get('id') for d in enabled} != CAMERAS:
            raise ValueError('unexpected camera set')
        for device in enabled:
            address = ipaddress.ip_address(device.get('host', ''))
            if (address.version != 4 or not any(address in lan for lan in PRIVATE_LANS)
                    or device.get('port') != 8080 or device.get('transport') != 'http_lab'):
                raise ValueError('unsafe endpoint')
            conn = connection_factory(str(address), 8080, timeout=4)
            try:
                conn.request('GET', '/api/v1/health', headers={'Accept': 'application/json'})
                response = conn.getresponse()
                payload = response.read(65537)
                if response.status != 200 or len(payload) > 65536:
                    raise ValueError('health unavailable')
                health = json.loads(payload)
                if (not isinstance(health, dict) or health.get('device_id') != device['id'].removeprefix('cam-')
                        or health.get('state') not in {'idle', 'armed_idle', 'stopped'}):
                    raise ValueError('camera not confirmed idle')
            finally:
                conn.close()
    except (OSError, ValueError, TypeError, KeyError, http.client.HTTPException) as exc:
        raise RuntimeError('CLEANUP_CAMERA_IDLE_NOT_VERIFIED') from exc


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
    if not isinstance(row["result_path"], str) or not isinstance(row["result_sha256"], str):
        raise RuntimeError("ANALYSIS_RESULT_REFERENCE_MISSING")
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


def candidate_times(db: sqlite3.Connection) -> tuple[list[float], int]:
    """Return global candidate/uncertain times that protect every camera.

    An unreadable successful detector result is uncertainty, not a negative.
    Its segment timestamp therefore protects the surrounding interval across
    all cameras. If the time itself cannot be parsed, fail closed globally.
    """
    protected: list[float] = []
    unreadable = 0
    rows = db.execute(
        "SELECT j.device_id,j.segment_id,j.video_sha256,d.input_sha256 AS detector_input_sha256,"
        "d.result_path,d.result_sha256 "
        "FROM jobs j LEFT JOIN detector_runs d ON d.job_id=j.id "
        "WHERE j.state='SUCCEEDED' ORDER BY j.id"
    )
    for row in rows:
        device, segment = row["device_id"], row["segment_id"]
        if device not in CAMERAS:
            continue
        when = segment_epoch(segment)
        if when is None:
            raise RuntimeError("SUCCESSFUL_ANALYSIS_TIME_UNPARSEABLE")
        try:
            doc = read_result(row)
        except (OSError, ValueError, RuntimeError):
            protected.append(when)
            unreadable += 1
            continue
        if doc["results"]:
            protected.append(when)
    return protected, unreadable


def camera_analysis_times(db: sqlite3.Connection) -> dict[str, list[float]]:
    """Successful camera jobs used to prove cross-camera coverage."""
    times = {camera: [] for camera in CAMERAS}
    rows = db.execute(
        "SELECT device_id,segment_id FROM jobs WHERE state='SUCCEEDED' "
        "AND device_id IN (?,?,?)",
        tuple(sorted(CAMERAS)),
    )
    for row in rows:
        when = segment_epoch(row["segment_id"])
        if when is None:
            raise RuntimeError("SUCCESSFUL_ANALYSIS_TIME_UNPARSEABLE")
        times[row["device_id"]].append(when)
    return times


def pending_analysis_times(db: sqlite3.Connection) -> list[float]:
    """Times of known camera segments not yet successfully analyzed."""
    placeholders = ",".join("?" for _ in CAMERAS)
    rows = db.execute(
        "SELECT device_id,segment_id,state FROM jobs "
        f"WHERE device_id IN ({placeholders}) AND state<>'SUCCEEDED'",
        tuple(sorted(CAMERAS)),
    )
    pending: list[float] = []
    for row in rows:
        when = segment_epoch(row["segment_id"])
        if when is None:
            raise RuntimeError("PENDING_ANALYSIS_TIME_UNPARSEABLE")
        pending.append(when)
    return pending


def time_is_protected(when: float, protected_times: list[float]) -> bool:
    return any(abs(when - protected_when) <= ADJACENT_SECONDS
               for protected_when in protected_times)


def missing_camera_boundary_coverage(when: float, camera_times: dict[str, list[float]]) -> list[str]:
    missing = []
    for camera in sorted(CAMERAS):
        starts = camera_times.get(camera, [])
        for offset, label in ((-SEGMENT_TARGET_SECONDS, "previous"),
                              (0, "current"),
                              (SEGMENT_TARGET_SECONDS, "next")):
            if not any(abs(stamp - (when + offset)) <= COVERAGE_ALIGNMENT_SECONDS
                       for stamp in starts):
                missing.append(f"{camera}:{label}")
    return missing


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
    if read_only:
        connection = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True, timeout=10)
    else:
        connection = sqlite3.connect(DB, timeout=10)
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
    if os.name == "posix" and (STATE.stat().st_uid != os.getuid() or STATE.stat().st_mode & 0o077):
        raise RuntimeError("RETENTION_STATE_DIRECTORY_PERMISSIONS_UNSAFE")
    if RECEIPTS.is_symlink():
        raise RuntimeError("RETENTION_RECEIPT_IS_SYMLINK")
    if doc.get("disposition") == "NO_CANDIDATE_DELETED" and RECEIPTS.is_file():
        wanted = (doc.get("device_id"), doc.get("segment_id"), doc.get("sha256"))
        with RECEIPTS.open("r", encoding="utf-8") as existing:
            for line in existing:
                try:
                    prior = json.loads(line)
                except ValueError:
                    continue
                if (prior.get("disposition") == "NO_CANDIDATE_DELETED" and
                        (prior.get("device_id"), prior.get("segment_id"), prior.get("sha256")) == wanted):
                    return
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Report eligible clips without deleting (default)")
    mode.add_argument("--apply", action="store_true", help="Delete eligible negative MP4 files")
    parser.add_argument("--max-deletes", type=int, default=1,
                        help="Bound apply mode to 1..5 deletions per invocation (default: 1)")
    parser.add_argument("--max-eligible", type=int, default=MAX_DRY_RUN_CANDIDATES,
                        help="Bound dry-run SHA checks/reporting to 1..5 eligible MP4s (default: 5)")
    parser.add_argument("--only-device", choices=sorted(CAMERAS),
                        help="Review one exact camera segment; apply mode requires max-deletes=1")
    parser.add_argument("--only-segment", help="Segment ID paired with --only-device")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.max_deletes <= MAX_APPLY_PER_RUN:
        parser.error(f"--max-deletes must be between 1 and {MAX_APPLY_PER_RUN}")
    if not 1 <= args.max_eligible <= MAX_DRY_RUN_CANDIDATES:
        parser.error(f"--max-eligible must be between 1 and {MAX_DRY_RUN_CANDIDATES}")
    if bool(args.only_device) != bool(args.only_segment):
        parser.error("--only-device and --only-segment must be provided together")
    if args.only_segment and not SEGMENT_RE.fullmatch(args.only_segment):
        parser.error("--only-segment must be a safe MeteorNode segment ID")
    if args.apply and args.only_segment and args.max_deletes != 1:
        parser.error("targeted apply must use --max-deletes 1")
    args.dry_run = not args.apply
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise RuntimeError("DO_NOT_RUN_RETENTION_AS_ROOT")
    if args.apply:
        require_capture_quiet()
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
            protected_times, unreadable_results = candidate_times(db)
            pending_times = pending_analysis_times(db)
            camera_times = camera_analysis_times(db)
            disposition_join = (
                "LEFT JOIN source_dispositions sd ON sd.device_id=j.device_id AND sd.segment_id=j.segment_id"
                if has_table else ""
            )
            disposition_select = (
                "sd.sha256 AS disposition_sha256,sd.disposition AS disposition,"
                "sd.deleted_utc AS disposition_deleted_utc"
                if has_table else "NULL AS disposition_sha256,NULL AS disposition,"
                "NULL AS disposition_deleted_utc"
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
            if args.only_segment:
                rows = [row for row in rows if row["device_id"] == args.only_device and
                        row["segment_id"] == args.only_segment]
                if not rows:
                    raise RuntimeError("TARGETED_SEGMENT_NOT_FOUND_IN_POST_ACTIVATION_SUCCESSFUL_ANALYSIS")

            counts = {"eligible": 0, "deleted": 0, "candidate_preserved": 0,
                      "adjacent_preserved": 0, "analysis_pending": 0,
                      "camera_coverage_incomplete": 0, "skipped": 0}
            stopped_at_limit = False
            for row in rows:
                device, segment = row["device_id"], row["segment_id"]
                if device not in CAMERAS:
                    continue
                existing = row["disposition"]
                if existing in {"NO_CANDIDATE_DELETED", "CANDIDATE_PRESERVED",
                                "ADJACENT_CANDIDATE_PRESERVED"}:
                    if (existing == "NO_CANDIDATE_DELETED" and not args.dry_run and
                            row["disposition_sha256"] == row["video_sha256"]):
                        try:
                            video, _sidecar = source_paths(device, segment)
                            if not video.exists():
                                append_receipt({"schema": "meteormesh.negative-retention.v1",
                                                "disposition": "NO_CANDIDATE_DELETED",
                                                "device_id": device, "segment_id": segment,
                                                "sha256": row["video_sha256"], "bytes": row["size_bytes"],
                                                "analysis_job_id": row["job_id"],
                                                "result_sha256": row["result_sha256"],
                                                "deleted_utc": row["disposition_deleted_utc"],
                                                "receipt_recovered": True})
                        except (OSError, RuntimeError) as exc:
                            print(f"RETENTION_RECEIPT_RECOVERY_SKIP {device}/{segment}: {type(exc).__name__}")
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

                if existing == "DELETE_PENDING" and row["disposition_sha256"] != row["video_sha256"]:
                    counts["skipped"] += 1
                    print(f"RETENTION_SKIP {device}/{segment}: pending SHA changed")
                    continue

                try:
                    video, sidecar = source_paths(device, segment)
                except RuntimeError as exc:
                    counts["skipped"] += 1
                    print(f"RETENTION_SKIP {device}/{segment}: {type(exc).__name__}:{exc}")
                    continue

                if existing == "DELETE_PENDING" and not video.exists():
                    deleted_utc = row["disposition_deleted_utc"] or now_utc()
                    if args.dry_run:
                        counts["skipped"] += 1
                        print(f"RETENTION_DRY_RUN_PENDING_RECOVERY {device}/{segment}")
                        continue
                    else:
                        write_disposition(db, row, "NO_CANDIDATE_DELETED", deleted_utc)
                        append_receipt({"schema": "meteormesh.negative-retention.v1",
                                        "disposition": "NO_CANDIDATE_DELETED",
                                        "device_id": device, "segment_id": segment,
                                        "sha256": row["video_sha256"], "bytes": row["size_bytes"],
                                        "analysis_job_id": row["job_id"],
                                        "result_sha256": row["result_sha256"],
                                        "deleted_utc": deleted_utc,
                                        "recovered_after_interruption": True})
                    counts["deleted"] += 1
                    print(f"RETENTION_RECOVERED_DELETE {device}/{segment}")
                    if not args.dry_run and counts["deleted"] >= args.max_deletes:
                        stopped_at_limit = True
                        break
                    continue

                if result["results"]:
                    if not args.dry_run and existing not in {"NO_CANDIDATE_DELETED", "CANDIDATE_PRESERVED"}:
                        write_disposition(db, row, "CANDIDATE_PRESERVED")
                    counts["candidate_preserved"] += 1
                    continue

                when = segment_epoch(segment)
                if when is None:
                    counts["skipped"] += 1
                    print(f"RETENTION_SKIP {device}/{segment}: segment time unavailable")
                    continue
                near_candidate = time_is_protected(when, protected_times)
                if near_candidate:
                    if not args.dry_run and existing not in {"NO_CANDIDATE_DELETED", "ADJACENT_CANDIDATE_PRESERVED"}:
                        write_disposition(db, row, "ADJACENT_CANDIDATE_PRESERVED")
                    counts["adjacent_preserved"] += 1
                    continue

                pending_nearby = time_is_protected(when, pending_times)
                if pending_nearby:
                    counts["analysis_pending"] += 1
                    print(f"RETENTION_SKIP {device}/{segment}: nearby camera analysis pending")
                    continue

                uncovered = missing_camera_boundary_coverage(when, camera_times)
                if uncovered:
                    counts["camera_coverage_incomplete"] += 1
                    print(f"RETENTION_SKIP {device}/{segment}: nearby successful analysis missing for "
                          f"{','.join(uncovered)}")
                    continue

                try:
                    if not sidecar.is_file() or sidecar.stat().st_dev != DATA.stat().st_dev:
                        raise RuntimeError("SIDECAR_MISSING_OR_UNSAFE")
                    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
                    if (metadata.get("segment_id") != segment or
                            metadata.get("device_id") != device.removeprefix("cam-") or
                            metadata.get("sha256") != row["video_sha256"] or
                            metadata.get("size_bytes") != row["size_bytes"]):
                        raise RuntimeError("SIDECAR_IDENTITY_MISMATCH")
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
                    if counts["eligible"] >= args.max_eligible:
                        stopped_at_limit = True
                        break
                    continue

                if existing is None:
                    require_capture_quiet()
                    write_disposition(db, row, "DELETE_PENDING")
                require_capture_quiet()
                video.unlink()
                if hasattr(os, "O_DIRECTORY"):
                    directory_fd = os.open(video.parent, os.O_RDONLY | os.O_DIRECTORY)
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
                if counts["deleted"] >= args.max_deletes:
                    stopped_at_limit = True
                    break
            print("NEGATIVE_RETENTION: " + json.dumps(
                {"mode": "DRY_RUN" if args.dry_run else "APPLY",
                 "target": ({"device_id": args.only_device, "segment_id": args.only_segment}
                            if args.only_segment else None),
                 "candidate_guard_intervals": len(protected_times),
                 "unreadable_successful_results_protected": unreadable_results,
                 "stopped_at_limit": stopped_at_limit, **counts}, sort_keys=True))
            return 0
        finally:
            db.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"NEGATIVE_RETENTION: ERROR {type(exc).__name__}:{str(exc)[:200]}", file=sys.stderr)
        raise SystemExit(2)
