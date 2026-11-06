#!/usr/bin/python3
"""RC193 post-analysis gate: preserve backup/report, bound disabled-policy dry-runs."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RELEASE = Path.home() / ".local/share/meteormesh/releases/rc198-retention-coverage-20261105"
NEW_HOOK = RELEASE / "after_analysis_with_backup.py"
DB_BACKUP = Path.home() / ".local/share/meteormesh/releases/rc167-20261104-bundler-hook/meteor_db_backup.py"


def main() -> int:
    if os.environ.get("SERVICE_RESULT", "") == "exec-condition":
        try:
            child = subprocess.run(
                ["/usr/bin/python3", "-B", str(DB_BACKUP)],
                capture_output=True, text=True, timeout=180, check=False,
            )
            for line in child.stdout.splitlines():
                print(line, flush=True)
            status = "OK" if child.returncode == 0 and "MM153_DB_BACKUP: PASS" in child.stdout else "ERROR"
            if child.stderr:
                print("RC191_BACKUP_STDERR: " + child.stderr[:240].replace("\n", " "), flush=True)
            print("RC191_POST_ANALYSIS: SKIPPED_EXEC_CONDITION BACKUP=" + status, flush=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print("RC191_POST_ANALYSIS: SKIPPED_EXEC_CONDITION BACKUP_ERROR " + type(exc).__name__, flush=True)
        return 0
    try:
        child = subprocess.run(
            ["/usr/bin/python3", "-B", str(NEW_HOOK)],
            capture_output=True, text=True, timeout=1800, check=False,
        )
        for line in child.stdout.splitlines():
            print(line, flush=True)
        if child.stderr:
            print("RC191_POST_ANALYSIS_STDERR: " + child.stderr[:240].replace("\n", " "), flush=True)
        print("RC191_POST_ANALYSIS: DELEGATED exit=" + str(child.returncode), flush=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("RC191_POST_ANALYSIS: DELEGATE_ERROR " + type(exc).__name__, flush=True)
    return 0  # Never mask the analyzer's exit result.


if __name__ == "__main__":
    raise SystemExit(main())
