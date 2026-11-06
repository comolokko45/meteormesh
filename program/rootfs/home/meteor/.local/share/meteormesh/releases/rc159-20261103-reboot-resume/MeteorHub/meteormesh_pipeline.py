#!/usr/bin/env python3
"""MeteorMesh 0.2 offline, deterministic Linux analysis tools.

No Android write endpoint, purge, Drive upload, LLM execution, or arbitrary shell.
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from hub_events import build_event_report
from hub_workflow import (claim_next, enqueue_verified, init_pipeline, mark_failed,
                          queue_summary, reclaim_stale, retry_failed, run_one_detector)
from meteormesh_hub import create_database


from mm01_bridge import guarded_db_connect as _mm01_db, verify_write_path as _mm01_verify, is_unsafe_symlink as _mm01_unsafe_link, guarded_partial_open as _mm01_open_partial, guarded_output_dir as _mm01_output_dir, guarded_detector_run as _mm01_detector_run, guarded_publish as _mm01_publish, is_protected_write_context as _mm01_active
def database_for(data_dir: Path, initialize: bool = False, read_only: bool = False) -> sqlite3.Connection:
    _mm01_verify(data_dir)
    if _mm01_unsafe_link(data_dir):
        raise ValueError("Data root must not be a symlink")
    path = data_dir / "meteorhub.sqlite3"
    if read_only:
        return _mm01_db(path, read_only=True)
    if path.is_symlink():
        raise ValueError("Database must not be a symlink")
    if initialize:
        data_dir.mkdir(parents=True, exist_ok=True)
        db = create_database(path)
        init_pipeline(db)
        return db
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError("Run 'init' against the data directory first")
    db = _mm01_db(path)
    db.execute("PRAGMA busy_timeout=30000")
    return db


def sha256_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing or unsafe configuration: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True,
                        help="MeteorHub directory containing meteorhub.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create or upgrade SQLite schema without touching recordings")
    sub.add_parser("status", help="Print queue and segment counts")
    for command in ("enqueue", "work"):
        command_parser = sub.add_parser(command)
        command_parser.add_argument("--metdet-config", type=Path, required=True)
        if command == "work":
            command_parser.add_argument("--metdet-repo", type=Path, required=True)
            command_parser.add_argument("--limit", type=int, default=1)
            command_parser.add_argument("--timeout-seconds", type=int, default=3600)
    sub.add_parser("retry-failed", help="Retry jobs marked FAILED; never deletes recordings")
    report = sub.add_parser("events", help="Save provisional event report; original MP4s untouched")
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--devices", default="cam-ruby,cam-viva,cam-surya")
    report.add_argument("--window-ms", type=int, default=2000)
    args = parser.parse_args(argv)
    # MM01_GUARDED_CLI_V0_4
    from contextlib import nullcontext as _mm01_nullcontext
    from mm01_bridge import guarded_cli as _mm01_guarded_cli
    _mm01_mode = ('pipeline-write' if args.command in ('init','status','enqueue','work','retry-failed','events') else None)
    with (_mm01_guarded_cli(args, mode=_mm01_mode) if _mm01_mode else _mm01_nullcontext()):

        with database_for(args.data_dir, initialize=args.command == "init", read_only=args.command == "status") as db:
            if args.command == "init":
                print(json.dumps({"schema_initialized": True, "auto_delete": False}))
            elif args.command == "status":
                print(json.dumps(queue_summary(db), indent=2))
            elif args.command == "enqueue":
                print(json.dumps({"jobs_enqueued": enqueue_verified(db, sha256_file(args.metdet_config))}))
            elif args.command == "retry-failed":
                print(json.dumps({"jobs_requeued": retry_failed(db)}))
            elif args.command == "events":
                devices = [s.strip() for s in args.devices.split(",")]
                if not devices or len(devices) != len(set(devices)):
                    raise ValueError("Camera list must have distinct device IDs")
                report_data = build_event_report(db, args.data_dir, devices, args.window_ms)
                # x mode refuses to overwrite prior reviews or hand-edited annotations.
                _mm01_verify(args.output)
                with args.output.open("x", encoding="utf-8") as stream:
                    json.dump(report_data, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                print(json.dumps({"report": str(args.output),
                                  "provisional_groups": report_data["provisional_groups"]}))
            elif args.command == "work":
                if not 1 <= args.limit <= 100:
                    raise ValueError("Worker limit must be between 1 and 100")
                reclaimed = reclaim_stale(db)
                completed = 0
                errors = []
                for _ in range(args.limit):
                    job = claim_next(db)
                    if job is None:
                        break
                    try:
                        result = run_one_detector(db, job, args.data_dir, args.metdet_repo,
                                                  args.metdet_config, args.timeout_seconds)
                        print(json.dumps({"job": job["id"], "result": result}))
                        completed += 1
                    except Exception as exc:
                        mark_failed(db, job["id"], str(exc))
                        errors.append({"job": job["id"], "error": str(exc)})
                print(json.dumps({"completed": completed, "reclaimed_stale": reclaimed,
                                  "errors": errors}))
                return 1 if errors else 0
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, FileNotFoundError, sqlite3.Error,
            json.JSONDecodeError) as exc:
        print(f"MeteorMesh pipeline: {exc}", file=sys.stderr)
        sys.exit(2)
