#!/usr/bin/env python3
"""Safe deterministic morning ingest/analyze/report runner for MeteorMesh.

No agent, Android write endpoint, file deletion, Drive upload, or shell command.
An interrupted run can be retried; each phase validates previous work.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import shutil
import sqlite3
import sys
import time
import uuid
from pathlib import Path

from hub_events import build_event_report
from hub_workflow import (claim_next, enqueue_verified, mark_failed, queue_summary,
                          reclaim_stale, run_one_detector, verify_metdet_repo)
from meteor_agent import answer
from meteor_health import summarize_health
from meteormesh_hub import process_device, read_device_config
from meteormesh_pipeline import database_for, sha256_file


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def validate_root(folder: Path) -> None:
    if folder.is_symlink():
        raise RuntimeError("Data directory symlink is forbidden")
    folder.mkdir(parents=True, exist_ok=True)
    if not folder.is_dir():
        raise RuntimeError("Data directory must be a directory")


@contextlib.contextmanager
def exclusive_run_lock(folder: Path):
    """Protect against simultaneous timer and manual invocations, without deletion."""
    lock = folder / ".meteor-morning.lock"
    if lock.is_symlink() or (lock.exists() and not lock.is_file()):
        raise RuntimeError("Unsafe morning lock path")
    # O_NOFOLLOW prevents a symlink-swap before opening the lock.
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another MeteorMesh morning run is already active") from exc
        yield


def reports_folder(data_root: Path) -> Path:
    folder = data_root / "morning-reports"
    if folder.is_symlink():
        raise RuntimeError("Report directory symlink is forbidden")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write_new_report(folder: Path, name: str, contents: dict) -> Path:
    """Write a fresh, non-authoritative JSON report without replacing older runs."""
    if "/" in name or "\\" in name or name.startswith(".") or not name.endswith(".json"):
        raise ValueError("Unsafe report name")
    final = folder / name
    temporary = folder / ("." + name + ".pending")
    if final.exists() or final.is_symlink() or temporary.exists() or temporary.is_symlink():
        raise RuntimeError("Report already exists; refusing overwrite")
    with temporary.open("x", encoding="utf-8") as out:
        json.dump(contents, out, indent=2, ensure_ascii=False)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    # Name has UTC microseconds plus random suffix; collisions are blocked above.
    # Atomic no-clobber publication, even if a report with this name appears
    # between our existence check and publication.
    os.link(temporary, final)
    temporary.unlink()  # Only our own now-duplicated pending report.
    return final


def plan(config: Path, data_root: Path, detector_repo: Path | None,
         detector_config: Path | None) -> dict:
    """No network, no state changes, no token contents exposed."""
    validation_error = None
    try:
        devices = read_device_config(config)
        device_ids = [d["id"] for d in devices]
    except ValueError as exc:
        # Planning is useful BEFORE the phones exist, when the example JSON
        # deliberately contains placeholder TLS fingerprints. Run stays strict.
        validation_error = str(exc)
        raw = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("devices"), list):
            raise ValueError("Device config must contain a devices array") from exc
        devices = [d for d in raw["devices"] if isinstance(d, dict)]
        device_ids = [str(d.get("id", "UNKNOWN"))[:60] for d in devices]
    return {
        "operation": "meteor_morning.plan",
        "runs_now": False,
        "data_root": str(data_root),
        "device_ids": device_ids,
        "device_count": len(devices),
        "config_ready_for_live_run": validation_error is None,
        "config_validation_error": validation_error,
        "ffprobe_found": bool(shutil.which("ffprobe")),
        "detector_repo_present": detector_repo is not None and detector_repo.is_dir(),
        "detector_config_present": detector_config is not None and detector_config.is_file(),
        "phases": ["sequential_pinned_tls_sync", "sha256_and_ffprobe", "sqlite_enqueue",
                   "single_worker_metdetpy", "provisional_report_if_complete"],
        "model_required": False,
        "auto_purge": False,
        "remote_upload": False,
        "warning": "Real phone/Linux end-to-end acceptance is still required",
    }


def run_morning(*, config: Path, data_root: Path, detector_repo: Path,
                detector_config: Path, max_jobs: int = 288,
                max_runtime_seconds: int = 12 * 3600,
                detector_timeout_seconds: int = 1800,
                min_free_bytes: int = 2 * 1024**3,
                clock=time.monotonic) -> dict:
    """Make progress despite one offline phone, but fail closed on corrupt sources."""
    if not 1 <= max_jobs <= 1000:
        raise ValueError("max_jobs must be between 1 and 1000")
    if not 60 <= detector_timeout_seconds < 6 * 3600:
        raise ValueError("detector timeout must be 60..21599 seconds")
    if max_runtime_seconds < 60 or min_free_bytes < 0:
        raise ValueError("Invalid runtime/disk reserve")
    devices = read_device_config(config)
    if not devices:
        raise ValueError("At least one enabled device is required")
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe required: never claim ingest verified without media check")
    validate_root(data_root)
    started = utc_now()
    start_monotonic = clock()
    deadline = start_monotonic + max_runtime_seconds
    run_id = (dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
              + "_" + uuid.uuid4().hex[:8])
    report: dict = {
        "schema": "meteormesh.morning.v0.7",
        "run_id": run_id,
        "started_utc": started,
        "devices": [],
        "sync_good": 0,
        "sync_pending_or_failed": 0,
        "jobs_enqueued": 0,
        "jobs_completed_this_run": 0,
        "jobs_failed_this_run": 0,
        "errors": [],
        "scientific_event_status": "PROVISIONAL_ONLY",
        "analysis_scope": "all_verified_segments_in_db_not_one_closed_night",
        "new_night_completeness_proven": False,
        "retention_enabled": False,
        "phone_delete_enabled": False,
        "hub_delete_enabled": False,
        "archive_verified": False,
        "session_analysis_closed": False,
        "llm_required": False,
    }

    with exclusive_run_lock(data_root):
        with database_for(data_root, initialize=True) as db:
            for device in devices:
                if clock() >= deadline:
                    report["errors"].append("Runtime budget exhausted before all phones")
                    report["devices"].append({"id": device["id"], "state": "SKIPPED_TIME_BUDGET"})
                    continue
                try:
                    good, pending = process_device(
                        device, data_root, db, False, min_free_bytes=min_free_bytes,
                        should_stop=lambda: clock() >= deadline,
                    )
                    report["sync_good"] += good
                    report["sync_pending_or_failed"] += pending
                    state = "INCOMPLETE" if pending else (
                        "NO_READY_SEGMENTS" if good == 0 else "OK")
                    report["devices"].append({"id": device["id"], "state": state, "verified": good,
                        "pending_or_failed": pending})
                    if pending:
                        report["errors"].append(f"{device['id']}: {pending} ingest attempts incomplete")
                    elif good == 0:
                        report["errors"].append(
                            f"{device['id']}: no READY segments; earlier nights in SQLite do not prove capture")
                except Exception as exc:
                    report["devices"].append({"id": device["id"], "state": "OFFLINE_OR_ERROR"})
                    report["errors"].append(f"{device['id']}: {type(exc).__name__}: {exc}")

            detector_ready = False
            if clock() < deadline:
                try:
                    digest = sha256_file(detector_config)
                    report["jobs_enqueued"] = enqueue_verified(db, digest)
                    # Verify release/version BEFORE claiming jobs, so an absent
                    # detector leaves queued jobs WAITING, not FAILED.
                    verify_metdet_repo(detector_repo)
                    detector_ready = True
                except Exception as exc:
                    report["errors"].append(f"detector preflight: {type(exc).__name__}: {exc}")

            if detector_ready:
                report["stale_jobs_reclaimed"] = reclaim_stale(db)
                for _ in range(max_jobs):
                    remaining = int(deadline - clock())
                    if remaining < 60:
                        break
                    job = claim_next(db)
                    if job is None:
                        break
                    try:
                        # Deadline can stop the next job from starting; never
                        # interrupt the downloader mid-file or erase originals.
                        run_one_detector(db, job, data_root, detector_repo, detector_config,
                                         timeout_seconds=min(detector_timeout_seconds, remaining))
                        report["jobs_completed_this_run"] += 1
                    except Exception as exc:
                        mark_failed(db, job["id"], str(exc))
                        report["jobs_failed_this_run"] += 1
                        report["errors"].append(f"job {job['id']}: {type(exc).__name__}: {exc}")

            report["queue"] = queue_summary(db)
            report["briefing"] = answer(db, "briefing")
            # Diagnostics only: includes previous nights and never grants purge.
            report["health_inventory"] = summarize_health(db, [d["id"] for d in devices])
            # Online phones with zero READY recordings are not evidence that
            # a night was captured and analyzed successfully.
            verified_total = report["queue"]["ingest_states"].get("INGEST_VERIFIED", 0)
            report["verified_segments_in_db"] = verified_total
            if verified_total == 0:
                report["errors"].append("No verified video segments; capture not proven")
            all_devices_ok = all(item.get("state") == "OK" for item in report["devices"])
            jobs = report["queue"]["jobs"]
            all_jobs_terminal_success = all(jobs.get(k, 0) == 0 for k in ("WAITING", "RUNNING", "FAILED"))
            if verified_total and all_devices_ok and all_jobs_terminal_success and detector_ready:
                try:
                    events = build_event_report(db, data_root, [d["id"] for d in devices])
                    event_name = "events_provisional_" + run_id + ".json"
                    report["provisional_event_report"] = str(write_new_report(
                        reports_folder(data_root), event_name, events))
                    report["provisional_events_unreviewed"] = events["provisional_groups"]
                except Exception as exc:
                    report["errors"].append(f"event summary: {type(exc).__name__}: {exc}")
            else:
                report["provisional_events_skipped"] = "INGEST_OR_ANALYSIS_INCOMPLETE"

        report["finished_utc"] = utc_now()
        report["elapsed_seconds"] = max(0, round(clock() - start_monotonic, 2))
        report["outcome"] = "PARTIAL_REVIEW_REQUIRED" if (
            report["errors"] or report.get("provisional_events_skipped")) else "COMPLETED_PROVISIONAL"
        report["run_report"] = str(reports_folder(data_root) / ("morning_" + run_id + ".json"))
        write_new_report(reports_folder(data_root), "morning_" + run_id + ".json", report)
    return report


def last_run(data_root: Path) -> dict:
    folder = data_root / "morning-reports"
    if folder.is_symlink() or not folder.is_dir():
        raise FileNotFoundError("No morning reports yet")
    candidates = sorted(folder.glob("morning_*.json"))
    if not candidates:
        raise FileNotFoundError("No morning reports yet")
    latest = candidates[-1]
    if latest.is_symlink():
        raise RuntimeError("Unsafe report symlink")
    return json.loads(latest.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "run"):
        p = sub.add_parser(command)
        p.add_argument("--config", type=Path, required=True)
        p.add_argument("--data-dir", type=Path, required=True)
        p.add_argument("--metdet-repo", type=Path, required=command == "run")
        p.add_argument("--metdet-config", type=Path, required=command == "run")
        if command == "run":
            p.add_argument("--max-jobs", type=int, default=288)
            p.add_argument("--max-runtime-hours", type=float, default=12.0)
            p.add_argument("--detector-timeout-seconds", type=int, default=1800)
            p.add_argument("--min-free-gb", type=float, default=2.0)
    state = sub.add_parser("last-run")
    state.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "plan":
        result = plan(args.config, args.data_dir, args.metdet_repo, args.metdet_config)
    elif args.command == "run":
        if not (1 / 60 <= args.max_runtime_hours <= 48) or not (0 <= args.min_free_gb <= 1000):
            raise ValueError("Invalid runtime/reserve choice")
        result = run_morning(config=args.config, data_root=args.data_dir,
                             detector_repo=args.metdet_repo, detector_config=args.metdet_config,
                             max_jobs=args.max_jobs,
                             max_runtime_seconds=int(args.max_runtime_hours * 3600),
                             detector_timeout_seconds=args.detector_timeout_seconds,
                             min_free_bytes=int(args.min_free_gb * 1024**3))
    else:
        result = last_run(args.data_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "run" and result["outcome"] != "COMPLETED_PROVISIONAL":
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"MeteorMesh morning: {exc}", file=sys.stderr)
        sys.exit(2)
