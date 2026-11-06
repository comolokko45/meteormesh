#!/usr/bin/env python3
"""Back up SQLite, apply bounded negative retention, and publish the report."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

BASE = Path(__file__).resolve().parent
OBSERVER_DIR = (Path.home() / ".local/share/meteormesh/releases/"
                "rc167-20261104-bundler-hook")
RETENTION_POLICY = Path.home() / ".local/state/meteormesh/negative-video-retention-enabled.json"
BACKUP = OBSERVER_DIR / "meteor_db_backup.py"
REPORT = OBSERVER_DIR / "after_analysis_rc151.py"
RETENTION = Path.home() / ".local/share/meteormesh/releases/rc210-backup-proof-20261105/MeteorHub/meteor-negative-video-retention.py"


def retention_enabled() -> bool:
    if RETENTION_POLICY.is_symlink() or not RETENTION_POLICY.is_file():
        return False
    try:
        doc = json.loads(RETENTION_POLICY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(doc, dict) and
            doc.get("schema") == "meteormesh.negative-retention-activation.v1" and
            doc.get("auto_delete_enabled") is True)


def run_step(label: str, script: Path, args: list[str], timeout: int) -> tuple[str, str]:
    try:
        result = subprocess.run(
            ["/usr/bin/python3", "-B", str(script), *args],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        for line in result.stdout.splitlines():
            print(line, flush=True)
        if result.stderr:
            print("RC191_HOOK_STDERR: " + label + " " +
                  result.stderr[:240].replace("\n", " "), flush=True)
        status = "OK" if result.returncode == 0 else "ERROR_EXIT_" + str(result.returncode)
        if label == "DB_BACKUP_PRE_RETENTION" and (result.returncode != 0 or
                                      "MM153_DB_BACKUP: PASS" not in result.stdout.splitlines()):
            status = "ERROR_MISSING_BACKUP_RESULT"
        if label == "NEGATIVE_RETENTION" and result.returncode == 0 and not any(
                line.startswith("NEGATIVE_RETENTION:") for line in result.stdout.splitlines()):
            status = "ERROR_MISSING_RESULT"
        if label == "NEGATIVE_RETENTION" and result.returncode == 0:
            rows=[line for line in result.stdout.splitlines() if line.startswith('NEGATIVE_RETENTION:')]
            if rows==['NEGATIVE_RETENTION: SKIPPED_ANALYSIS_OR_SYNC_LOCKED']:
                status='DEFERRED'
            else:
                try:
                    if len(rows)!=1:raise ValueError('ambiguous result')
                    payload=json.loads(rows[0].split(': ',1)[1])
                    if not isinstance(payload,dict) or payload.get('mode') not in ('APPLY','DRY_RUN') or type(payload.get('deleted')) is not int or payload['deleted']<0:
                        raise ValueError('invalid result')
                except (ValueError,IndexError,TypeError):status='ERROR_INVALID_RESULT'
    except (OSError, subprocess.TimeoutExpired) as exc:
        result = None
        status = "ERROR_" + type(exc).__name__
    print("RC191_CHAIN: " + label + "=" + status, flush=True)
    output = result.stdout if result is not None else ""
    return status, output


def full_successful_batch(output: str) -> bool:
    lines = [line.split(": ", 1)[1] for line in output.splitlines()
             if line.startswith("NEGATIVE_RETENTION: {")]
    if len(lines) != 1:
        return False
    try:
        row = json.loads(lines[0])
    except ValueError:
        return False
    return (row.get("mode") == "APPLY" and type(row.get("deleted")) is int
            and row["deleted"] == 5 and row.get("stopped_at_limit") is True)


def main() -> int:
    # Two independently verified five-file batches; total upper bound ten.
    # Never bypass the unmodified RC210 per-file protection and lock checks.
    failed = False
    for batch in range(2):
        backup_status, _ = run_step("DB_BACKUP_PRE_RETENTION", BACKUP, [], 180)
        if backup_status != "OK":
            failed = True
            print("RC217: RETENTION_SKIPPED_BACKUP_NOT_VERIFIED", flush=True)
            break
        enabled = retention_enabled()
        args = (["--apply", "--max-deletes", "5"] if enabled
                else ["--dry-run", "--max-eligible", "5"])
        status, output = run_step("NEGATIVE_RETENTION", RETENTION, args, 120)
        if status not in ("OK", "DEFERRED"):
            failed = True
        print("RC217: batch=" + str(batch + 1) + " status=" + status, flush=True)
        if not enabled or status != "OK" or not full_successful_batch(output):
            break
    report_status, _ = run_step("REPORT", REPORT, [], 300)
    return 1 if failed or report_status != "OK" else 0


if __name__ == "__main__":
    raise SystemExit(main())
