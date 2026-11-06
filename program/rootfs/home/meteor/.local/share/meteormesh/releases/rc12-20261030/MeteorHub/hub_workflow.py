"""MeteorHub 0.2 deterministic analysis queue and MetDetPy adapter.

Only reads verified source MP4 files. It writes SQLite rows and detection results,
never changes a phone, uploads data, or deletes any recording.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

from meteornode_pull import VALID_ID, MeteorNode


METDETPY_VERSION = "v2.6.1"
METDETPY_COMMIT_PREFIX = "4825da0"  # Official upstream release tag, 9 Oct 2026.
DEFAULT_JOB_LEASE_SECONDS = 6 * 3600


from mm01_bridge import persistent_data_path as _mm01_persistent_path
from mm01_bridge import guarded_db_connect as _mm01_db, verify_write_path as _mm01_verify, is_unsafe_symlink as _mm01_unsafe_link, guarded_partial_open as _mm01_open_partial, guarded_output_dir as _mm01_output_dir, guarded_detector_run as _mm01_detector_run, guarded_publish as _mm01_publish, is_protected_write_context as _mm01_active
def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    return MeteorNode.file_sha256(path)


def init_pipeline(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unique_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL CHECK(kind IN ('DETECT')),
            device_id TEXT NOT NULL,
            segment_id TEXT NOT NULL,
            video_sha256 TEXT NOT NULL,
            detector_version TEXT NOT NULL,
            detector_config_sha256 TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('WAITING','RUNNING','SUCCEEDED','FAILED')),
            attempts INTEGER NOT NULL DEFAULT 0,
            claimed_at REAL,
            last_error TEXT,
            created_utc TEXT NOT NULL,
            finished_utc TEXT
        );
        CREATE TABLE IF NOT EXISTS detector_runs (
            job_id INTEGER NOT NULL UNIQUE,
            device_id TEXT NOT NULL,
            segment_id TEXT NOT NULL,
            input_sha256 TEXT NOT NULL,
            detector_version TEXT NOT NULL,
            detector_commit TEXT NOT NULL,
            config_sha256 TEXT NOT NULL,
            result_path TEXT NOT NULL,
            result_sha256 TEXT NOT NULL,
            duration_seconds REAL NOT NULL,
            completed_utc TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs(id)
        );
    """)


def enqueue_verified(db: sqlite3.Connection, config_sha: str) -> int:
    """Queue only files that passed both phone hash and ffprobe verification."""
    if len(config_sha) != 64 or any(c not in "0123456789abcdef" for c in config_sha):
        raise ValueError("config_sha must be a lowercase SHA-256 digest")
    rows = db.execute("""SELECT device_id, segment_id, sha256 FROM segments
                         WHERE state='INGEST_VERIFIED' ORDER BY device_id, segment_id""").fetchall()
    added = 0
    for device, segment, digest in rows:
        if not VALID_ID.fullmatch(device) or not VALID_ID.fullmatch(segment):
            raise ValueError("Stored device/segment name is unsafe")
        key_material = f"DETECT:{device}:{segment}:{digest}:{METDETPY_VERSION}:{config_sha}"
        unique = hashlib.sha256(key_material.encode()).hexdigest()
        cursor = db.execute("""INSERT OR IGNORE INTO jobs
            (unique_key, kind, device_id, segment_id, video_sha256, detector_version,
            detector_config_sha256, state, created_utc)
            VALUES (?, 'DETECT', ?, ?, ?, ?, ?, 'WAITING', ?)""",
            (unique, device, segment, digest, METDETPY_VERSION, config_sha, utc_now()))
        added += cursor.rowcount
    db.commit()
    return added


def reclaim_stale(db: sqlite3.Connection, lease_seconds: int = DEFAULT_JOB_LEASE_SECONDS,
                  now: float | None = None) -> int:
    if lease_seconds < 60:
        raise ValueError("Lease should never be under 60 seconds")
    now = time.time() if now is None else now
    cursor = db.execute("""UPDATE jobs SET state='WAITING', claimed_at=NULL,
        last_error='Requeued after expired worker lease'
        WHERE state='RUNNING' AND claimed_at IS NOT NULL AND claimed_at < ?""",
        (now - lease_seconds,))
    db.commit()
    return cursor.rowcount


def claim_next(db: sqlite3.Connection, now: float | None = None) -> dict | None:
    now = time.time() if now is None else now
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("""SELECT id, device_id, segment_id, video_sha256,
            detector_config_sha256 FROM jobs WHERE state='WAITING'
            ORDER BY id LIMIT 1""").fetchone()
        if row is None:
            db.commit()
            return None
        jobid, device, segment, digest, config_digest = row
        db.execute("""UPDATE jobs SET state='RUNNING', attempts=attempts+1,
                     claimed_at=?, last_error=NULL WHERE id=? AND state='WAITING'""", (now, jobid))
        db.commit()
        return {"id": jobid, "device_id": device, "segment_id": segment,
                "sha256": digest, "config_sha256": config_digest}
    except Exception:
        db.rollback()
        raise


def mark_failed(db: sqlite3.Connection, jobid: int, message: str) -> None:
    db.execute("""UPDATE jobs SET state='FAILED', claimed_at=NULL,
        last_error=?, finished_utc=? WHERE id=? AND state='RUNNING'""",
        (message[:2000], utc_now(), jobid))
    db.commit()


def retry_failed(db: sqlite3.Connection) -> int:
    cursor = db.execute("""UPDATE jobs SET state='WAITING', claimed_at=NULL,
        last_error=NULL, finished_utc=NULL WHERE state='FAILED'""")
    db.commit()
    return cursor.rowcount


def verify_metdet_repo(repo: Path) -> str:
    """Require the checked-out MetDetPy tree to match our pinned upstream commit."""
    entry = repo / "MetDetPy.py"
    if not entry.is_file() or not (repo / ".git").exists():
        raise RuntimeError("MetDetPy Git checkout with MetDetPy.py required")
    try:
        git = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=15, check=True)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Cannot verify MetDetPy git commit: {exc}") from exc
    commit = git.stdout.strip()
    if not commit.startswith(METDETPY_COMMIT_PREFIX):
        raise RuntimeError(f"MetDetPy not pinned to {METDETPY_VERSION}: found {commit}")
    return commit


def run_one_detector(db: sqlite3.Connection, job: dict, data_root: Path,
                     metdet_repo: Path, config: Path,
                     timeout_seconds: int = 3600, run_command=subprocess.run) -> str:
    """Execute exactly the pinned detector (no shell), then atomically publish valid MDRF."""
    _mm01_verify(data_root)
    from meteormesh_hub import verify_with_ffprobe

    metdet_repo = metdet_repo.resolve()
    config = config.resolve(strict=True)
    _mm01_verify(data_root)  # Never resolve a pinned /proc/self/fd root
    if timeout_seconds < 60 or timeout_seconds >= DEFAULT_JOB_LEASE_SECONDS:
        raise ValueError("Detector timeout must fit within the six-hour job lease")
    if sha256_file(config) != job["config_sha256"]:
        raise RuntimeError("Detector config changed since this job was enqueued")
    commit = verify_metdet_repo(metdet_repo)
    folder = data_root / job["device_id"]
    video = folder / f"{job['segment_id']}.mp4"
    if folder.is_symlink() or video.is_symlink() or not video.is_file():
        raise RuntimeError("Missing or unsafe input video")
    if sha256_file(video) != job["sha256"]:
        raise RuntimeError("Verified source MP4 changed since ingest")
    status, _ = verify_with_ffprobe(video)
    if status != "INGEST_VERIFIED":
        raise RuntimeError(f"Input MP4 fails media check: {status}")

    output_dir = data_root / "detector-results" / job["device_id"]
    if output_dir.is_symlink():
        raise RuntimeError("Unsafe detector results folder")
    output_dir = _mm01_output_dir(output_dir)
    final = output_dir / f"{job['segment_id']}_{job['config_sha256'][:10]}.json"
    if final.is_symlink():
        raise RuntimeError("Unsafe detection result target")
    if final.exists():
        # A previous crash may have published valid output before DB commit.
        mdrf = json.loads(final.read_text(encoding="utf-8"))
        if not isinstance(mdrf, dict) or not isinstance(mdrf.get("results"), list):
            raise RuntimeError("Conflicting existing detector result")
        raise RuntimeError("Existing detector result requires manual recovery; refusing overwrite")

    pending = output_dir / f".{job['segment_id']}_{uuid.uuid4().hex}.pending.json"
    # HP G62 has a dedicated venv; use it only when explicitly selected.
    detector_python = os.environ.get('METEORMESH_DETECTOR_PYTHON', sys.executable)
    exe = Path(detector_python)
    if not exe.is_absolute() or not exe.is_file() or not os.access(exe, os.X_OK):
        raise RuntimeError('METDETPY_PYTHON_NOT_EXECUTABLE')
    argv = [str(exe), str(metdet_repo / 'MetDetPy.py'), str(video),
            '--cfg', str(config), '--mode', 'backend', '--save-path', str(pending)]
    if os.environ.get('METEORMESH_DETECTOR_CPU_MODE') == '1':
        argv.extend(['--resize', '960', '--recheck', 'off', '--provider', 'cpu'])
    started = time.monotonic()
    try:
        result = _mm01_detector_run(run_command, argv, pending, metdet_repo, timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("MetDetPy timed out; original MP4 retained") from exc
    elapsed = time.monotonic() - started
    if result.returncode:
        raise RuntimeError(f"MetDetPy exit={result.returncode}: {result.stderr[-1000:]}")
    if not pending.is_file() or pending.is_symlink():
        raise RuntimeError("MetDetPy produced no safe MDRF JSON")
    doc = json.loads(pending.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
        raise RuntimeError("Unexpected MetDetPy result format")
    # Put a complete parsed file in place; never rename an incomplete or invalid output.
    if final.exists():
        raise RuntimeError("Refusing result file overwrite")
    _mm01_publish(pending, final)
    result_sha = sha256_file(final)
    db.execute("""INSERT INTO detector_runs
        (job_id, device_id, segment_id, input_sha256, detector_version,
         detector_commit, config_sha256, result_path, result_sha256,
         duration_seconds, completed_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (job["id"], job["device_id"], job["segment_id"], job["sha256"],
         METDETPY_VERSION, commit, job["config_sha256"], str(_mm01_persistent_path(final)), result_sha,
         elapsed, utc_now()))
    db.execute("""UPDATE jobs SET state='SUCCEEDED', claimed_at=NULL,
               finished_utc=? WHERE id=? AND state='RUNNING'""", (utc_now(), job["id"]))
    db.commit()
    return str(_mm01_persistent_path(final))


def queue_summary(db: sqlite3.Connection) -> dict:
    counts = {row[0]: row[1] for row in db.execute("SELECT state, COUNT(*) FROM jobs GROUP BY state")}
    ingest = {row[0]: row[1] for row in db.execute("SELECT state, COUNT(*) FROM segments GROUP BY state")}
    return {"ingest_states": ingest, "jobs": counts,
            "completed_detector_runs": db.execute("SELECT COUNT(*) FROM detector_runs").fetchone()[0],
            "retention_auto_delete": False}
