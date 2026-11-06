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
RETENTION = BASE / "MeteorHub" / "meteor-negative-video-retention.py"


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
        if label == "DB_BACKUP" and (result.returncode != 0 or
                                      "MM153_DB_BACKUP: PASS" not in result.stdout):
            status = "ERROR_MISSING_BACKUP_RESULT"
        if label == "NEGATIVE_RETENTION" and result.returncode == 0 and not any(
                line.startswith("NEGATIVE_RETENTION:") for line in result.stdout.splitlines()):
            status = "ERROR_MISSING_RESULT"
    except (OSError, subprocess.TimeoutExpired) as exc:
        result = None
        status = "ERROR_" + type(exc).__name__
    print("RC191_CHAIN: " + label + "=" + status, flush=True)
    output = result.stdout if result is not None else ""
    return status, output


def main() -> int:
    # A verified SQLite snapshot must exist before any destructive MP4 action.
    backup_status, _ = run_step("DB_BACKUP_PRE_RETENTION", BACKUP, [], 180)
    if backup_status == "OK":
        mode_args = ["--apply", "--max-deletes", "5"] if retention_enabled() else ["--dry-run"]
        run_step("NEGATIVE_RETENTION", RETENTION, mode_args, 900)
    else:
        print("RC191_CHAIN: NEGATIVE_RETENTION=SKIPPED_BACKUP_NOT_VERIFIED", flush=True)

    # Publish the live report after retention has updated tombstones/receipts.
    run_step("REPORT", REPORT, [], 900)
    return 0  # Post-run observers never mask the analyzer's result.


if __name__ == "__main__":
    raise SystemExit(main())
