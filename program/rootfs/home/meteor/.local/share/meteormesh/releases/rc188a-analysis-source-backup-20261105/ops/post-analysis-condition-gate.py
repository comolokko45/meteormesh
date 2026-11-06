#!/usr/bin/python3
"""RC188 ExecStopPost gate: do not run heavy observers after an ExecCondition skip."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

OLD_HOOK = Path(
    "/home/meteor/.local/share/meteormesh/releases/"
    "rc185-no-candidate-retention-20261104/after_analysis_with_backup.py"
)
DB_BACKUP = Path(
    "/home/meteor/.local/share/meteormesh/releases/"
    "rc167-20261104-bundler-hook/meteor_db_backup.py"
)


def main() -> int:
    result = os.environ.get("SERVICE_RESULT", "")
    if result == "exec-condition":
        # Keep the independent, verified SQLite backup cadence even when no
        # detector work can run. Skip only report and negative-retention work.
        try:
            child = subprocess.run(
                ["/usr/bin/python3", "-B", str(DB_BACKUP)],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            for line in child.stdout.splitlines():
                print(line, flush=True)
            if child.stderr:
                print("RC188_BACKUP_STDERR: " + child.stderr[:240].replace("\n", " "), flush=True)
            status = "OK" if child.returncode == 0 and "MM153_DB_BACKUP: PASS" in child.stdout else "ERROR"
            print("RC188_POST_ANALYSIS: SKIPPED_EXEC_CONDITION BACKUP=" + status, flush=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print("RC188_POST_ANALYSIS: SKIPPED_EXEC_CONDITION BACKUP_ERROR " + type(exc).__name__, flush=True)
        return 0
    try:
        child = subprocess.run(
            ["/usr/bin/python3", "-B", str(OLD_HOOK)],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        for line in child.stdout.splitlines():
            print(line, flush=True)
        if child.stderr:
            print("RC188_POST_ANALYSIS_STDERR: " + child.stderr[:240].replace("\n", " "), flush=True)
        print("RC188_POST_ANALYSIS: DELEGATED exit=" + str(child.returncode), flush=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("RC188_POST_ANALYSIS: DELEGATE_ERROR " + type(exc).__name__, flush=True)
    # Post-analysis observers must never change the detector's exit result.
    return 0


if __name__ == "__main__":
    sys.exit(main())
