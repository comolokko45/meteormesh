#!/usr/bin/env python3
"""Read-only status check for MeteorMesh HP G62 (MeteorHub 0.9).

Reads the existing SSD/HDD mount, disk availability, kernel memory counters,
systemd user-timer state, optional MeteorHub SQLite queue and latest morning
report. Does not run a detector, contact phones, create/modify data files,
delete recordings, run SMART/disk-health tests, or enable any systemd unit.

Only Python 3 standard library modules are required.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pwd
import sqlite3
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo


DEFAULT_DATA_ROOT = Path("/srv/meteor-data")
MORNING_TIMER = "meteormesh-morning.timer"
EXPECTED_HDD_UUID = "444a8a6d-090a-456c-8a9d-d99665c32574"
LIMIT_BYTES = 1024 * 1024  # Refuse unexpectedly large JSON reports.


def read_command(argv: list[str], timeout: float = 3) -> dict:
    try:
        completed = subprocess.run(argv, capture_output=True, text=True,
                                   check=False, timeout=timeout)
        return {"ok": completed.returncode == 0,
                "text": completed.stdout.strip()[:1000]}
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "text": ""}


def parse_properties(command: dict) -> dict[str, str]:
    return dict(line.split("=", 1) for line in command["text"].splitlines()
                if "=" in line)


def storage_details(path: Path, *, require_mount: bool) -> dict:
    info: dict = {"path": str(path), "exists": False, "is_mount": False,
                  "free_gib": None, "total_gib": None}
    if path.is_symlink() or not path.is_dir():
        info["reason"] = "MISSING_OR_SYMLINK"
        return info
    info["exists"] = True
    info["is_mount"] = os.path.ismount(path)
    if require_mount and not info["is_mount"]:
        info["reason"] = "NOT_MOUNTED_NO_FALLBACK_TO_SSD"
        return info
    try:
        stat = os.statvfs(path)
    except OSError:
        info["reason"] = "STATVFS_FAILED"
        return info
    info["free_gib"] = round(stat.f_bavail * stat.f_frsize / 1024**3, 2)
    info["total_gib"] = round(stat.f_blocks * stat.f_frsize / 1024**3, 2)
    return info


def memory_details() -> dict:
    values: dict[str, int] = {}
    try:
        with Path("/proc/meminfo").open(encoding="utf-8") as source:
            for line in source:
                name, sep, remainder = line.partition(":")
                if sep and name in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                    values[name] = int(remainder.strip().split()[0])
    except (OSError, ValueError, IndexError):
        return {"available_gib": None, "swap_used_gib": None}
    total_swap = values.get("SwapTotal", 0)
    return {"available_gib": round(values.get("MemAvailable", 0) / 1024**2, 2),
            "swap_used_gib": round((total_swap - values.get("SwapFree", total_swap)) / 1024**2, 2)}


def sqlite_details(root: Path) -> dict:
    db_path = root / "meteorhub.sqlite3"
    result: dict = {"state": "NOT_CREATED", "ingest": {}, "jobs": {},
                    "detector_runs": 0}
    if db_path.is_symlink():
        result["state"] = "UNSAFE_SYMLINK"
        return result
    if not db_path.is_file():
        return result
    try:
        # URI mode=ro never creates a missing database; query_only prevents SQL writes.
        with sqlite3.connect(db_path.resolve(strict=True).as_uri() + "?mode=ro",
                             uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "segments" in tables:
                result["ingest"] = dict(db.execute(
                    "SELECT state, COUNT(*) FROM segments GROUP BY state"))
            if "jobs" in tables:
                result["jobs"] = dict(db.execute(
                    "SELECT state, COUNT(*) FROM jobs GROUP BY state"))
            if "detector_runs" in tables:
                result["detector_runs"] = db.execute(
                    "SELECT COUNT(*) FROM detector_runs").fetchone()[0]
        result["state"] = "READ_ONLY_OK"
    except (sqlite3.Error, OSError, ValueError):
        result["state"] = "READ_ERROR"
    return result


def morning_details(root: Path) -> dict:
    folder = root / "morning-reports"
    result = {"state": "NO_REPORT_YET", "outcome": None, "error_count": None}
    if folder.is_symlink():
        return {**result, "state": "UNSAFE_SYMLINK"}
    if not folder.is_dir():
        return result
    try:
        matches = sorted(x for x in folder.glob("morning_*.json")
                         if x.is_file() and not x.is_symlink())
        if not matches:
            return result
        latest = matches[-1]
        if latest.stat().st_size > LIMIT_BYTES:
            return {**result, "state": "REPORT_TOO_LARGE"}
        with latest.open(encoding="utf-8") as stream:
            report = json.load(stream)
        if not isinstance(report, dict):
            return {**result, "state": "INVALID_REPORT"}
        errors = report.get("errors")
        return {"state": "READ_OK", "outcome": report.get("outcome"),
                "error_count": len(errors) if isinstance(errors, list) else None,
                "finished_utc": report.get("finished_utc")}
    except (OSError, ValueError, json.JSONDecodeError):
        return {**result, "state": "READ_ERROR"}


def collect(data_root: Path = DEFAULT_DATA_ROOT, *, min_hdd_free_gib: float = 20) -> dict:
    hdd = storage_details(data_root, require_mount=True)
    if hdd["is_mount"]:
        mount_reply = read_command(["findmnt", "-M", str(data_root), "-n",
                                    "-o", "SOURCE,FSTYPE,UUID"])
        mount_fields = mount_reply["text"].split()
        if mount_reply["ok"] and len(mount_fields) == 3:
            hdd.update({"source": mount_fields[0], "fstype": mount_fields[1],
                        "uuid": mount_fields[2]})
        else:
            hdd["mount_verification"] = "UNKNOWN"
    ssd = storage_details(Path("/"), require_mount=False)
    db = sqlite_details(data_root) if hdd["is_mount"] else {"state": "HDD_NOT_MOUNTED"}
    reports = morning_details(data_root) if hdd["is_mount"] else {"state": "HDD_NOT_MOUNTED"}
    timer_cmd = read_command(["systemctl", "--user", "show", MORNING_TIMER,
                              "-p", "LoadState", "-p", "ActiveState", "-p", "UnitFileState"])
    timer = parse_properties(timer_cmd)
    user = pwd.getpwuid(os.getuid()).pw_name
    linger = parse_properties(read_command(["loginctl", "show-user", user, "-p", "Linger"]))
    ntp = parse_properties(read_command(["timedatectl", "show", "-p", "NTPSynchronized"]))
    issues: list[dict[str, str]] = []
    if not hdd["is_mount"] or hdd["free_gib"] is None:
        issues.append({"level": "CRITICAL", "code": "HDD_NOT_MOUNTED"})
    else:
        if hdd.get("fstype") and hdd["fstype"] != "ext4":
            issues.append({"level": "CRITICAL", "code": "HDD_UNEXPECTED_FILESYSTEM"})
        if hdd.get("uuid") and data_root == DEFAULT_DATA_ROOT and hdd["uuid"] != EXPECTED_HDD_UUID:
            issues.append({"level": "CRITICAL", "code": "HDD_UNEXPECTED_UUID"})
        if hdd.get("mount_verification") == "UNKNOWN":
            issues.append({"level": "WARNING", "code": "HDD_MOUNT_SOURCE_UNVERIFIED"})
        if hdd["free_gib"] < 5:
            issues.append({"level": "CRITICAL", "code": "HDD_FREE_UNDER_5_GIB"})
        elif hdd["free_gib"] < min_hdd_free_gib:
            issues.append({"level": "WARNING", "code": "HDD_FREE_LOW"})
    if ssd["free_gib"] is not None and ssd["free_gib"] < 5:
        issues.append({"level": "WARNING", "code": "SSD_FREE_UNDER_5_GIB"})
    if db["state"] not in {"NOT_CREATED", "READ_ONLY_OK", "HDD_NOT_MOUNTED"}:
        issues.append({"level": "WARNING", "code": "DATABASE_" + db["state"]})
    if db.get("jobs", {}).get("FAILED", 0):
        issues.append({"level": "WARNING", "code": "DETECTOR_FAILED_JOBS"})
    if reports["state"] not in {"NO_REPORT_YET", "READ_OK", "HDD_NOT_MOUNTED"}:
        issues.append({"level": "WARNING", "code": "MORNING_" + reports["state"]})
    if reports.get("outcome") == "PARTIAL_REVIEW_REQUIRED":
        issues.append({"level": "WARNING", "code": "LAST_MORNING_PARTIAL"})
    if timer.get("UnitFileState") in {"enabled", "enabled-runtime"} and not db.get("ingest", {}).get("INGEST_VERIFIED", 0):
        issues.append({"level": "WARNING", "code": "MORNING_TIMER_ENABLED_BEFORE_FIRST_VERIFIED_VIDEO"})
    if linger.get("Linger") == "no":
        issues.append({"level": "WARNING", "code": "USER_LINGER_DISABLED"})
    if ntp.get("NTPSynchronized") == "no":
        issues.append({"level": "WARNING", "code": "CLOCK_NOT_SYNCHRONIZED"})
    status = "CRITICAL" if any(x["level"] == "CRITICAL" for x in issues) else (
        "WARNING" if issues else "SETUP_WAITING_FOR_PHONES" if db["state"] == "NOT_CREATED"
        else "OK")
    return {
        "schema": "meteormesh.local-status.v1",
        "checked_tr": dt.datetime.now(ZoneInfo("Europe/Istanbul")).isoformat(timespec="seconds"),
        "status": status,
        "ssd_root": ssd,
        "hdd": hdd,
        "memory": memory_details(),
        "linger": linger.get("Linger", "UNKNOWN"),
        "ntp_synchronized": ntp.get("NTPSynchronized", "UNKNOWN"),
        "morning_timer": {"load": timer.get("LoadState", "UNKNOWN"),
                          "active": timer.get("ActiveState", "UNKNOWN"),
                          "enabled": timer.get("UnitFileState", "UNKNOWN")},
        "database": db,
        "last_morning": reports,
        "issues": issues,
        "read_only": True,
        "runs_detector": False,
        "can_delete": False,
        "smart_disk_health_checked": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--min-hdd-free-gib", type=float, default=20.0)
    parser.add_argument("--strict", action="store_true", help="Exit nonzero on issues")
    args = parser.parse_args()
    if not 0 <= args.min_hdd_free_gib <= 1000:
        parser.error("Invalid free-space threshold")
    report = collect(args.data_dir, min_hdd_free_gib=args.min_hdd_free_gib)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict:
        return 2 if report["status"] == "CRITICAL" else 1 if report["status"] == "WARNING" else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
