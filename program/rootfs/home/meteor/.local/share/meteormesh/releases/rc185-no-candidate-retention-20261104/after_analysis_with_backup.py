#!/usr/bin/env python3
"""Run observers, dry-run negative retention, and the DB backup."""
from pathlib import Path
import subprocess
import sys

BASE = Path(__file__).resolve().parent
OBSERVER_DIR = (Path.home() / ".local/share/meteormesh/releases/"
                "rc167-20261104-bundler-hook")
STEPS = [
    ("REPORT", OBSERVER_DIR / "after_analysis_rc151.py", 900),
    ("NEGATIVE_RETENTION", BASE / "MeteorHub" / "meteor-negative-video-retention.py", 900),
    ("DB_BACKUP", OBSERVER_DIR / "meteor_db_backup.py", 180),
]


def main() -> int:
    for label, script, timeout in STEPS:
        try:
            result = subprocess.run(
                ["/usr/bin/python3", "-B", str(script)] + ( ["--dry-run"] if label == "NEGATIVE_RETENTION" else [] ),
                capture_output=True, text=True, timeout=timeout,
            )
            for line in result.stdout.splitlines():
                print(line, flush=True)
            if result.stderr:
                print("RC185_HOOK_STDERR: " + label + " " +
                      result.stderr[:240].replace("\n", " "), flush=True)
            status = "OK" if result.returncode == 0 else "ERROR_EXIT_" + str(result.returncode)
            if result.returncode == 0 and label == "NEGATIVE_RETENTION":
                if not any(line.startswith("NEGATIVE_RETENTION:") for line in result.stdout.splitlines()):
                    status = "ERROR_MISSING_RESULT"
            if result.returncode == 0 and label == "DB_BACKUP" and "MM153_DB_BACKUP: PASS" not in result.stdout:
                status = "ERROR_MISSING_BACKUP_RESULT"
        except (OSError, subprocess.TimeoutExpired) as exc:
            status = "ERROR_" + type(exc).__name__
        print("RC185_CHAIN: " + label + "=" + status, flush=True)
    return 0  # Post-run observers never mask the analyzer's result.


if __name__ == "__main__":
    sys.exit(main())
