#!/usr/bin/env python3
"""MeteorMesh operations: one-shot, standard-library, read-only host inventory.

Inspects systemd user unit properties, validated mount identity, known observer
paths, the newest existing SQLite-backup filename, and the newest morning report.
Does NOT open SQLite, probe phones, write files, run detection, change systemd,
restart services, or inspect credentials. Run with python3 -B.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

HOME = Path.home()
DATA = Path("/srv/meteor-data")
UUID = "444a8a6d-090a-456c-8a9d-d99665c32574"
UNITS = (
    "meteormesh-status.timer",
    "meteormesh-status.service",
    "meteormesh-morning.timer",
    "meteormesh-morning.service",
    "meteormesh-analyze.service",
    "meteormesh-capture.timer",
    "meteormesh-capture.service",
    "meteormesh-control-013.service",
)
READ_PROPS = ("LoadState", "ActiveState", "UnitFileState", "Result",
              "ExecMainStatus", "SuccessExitStatus", "ExecStart",
              "ExecStartPost", "ExecStopPost")
AGENT = HOME / ".local/lib/meteormesh/meteor-status-agent.py"
WATCH = HOME / ".local/lib/meteormesh/meteor-phone-watch.py"
REPORTS = DATA / "morning-reports"
BACKUPS = HOME / ".local/share/meteormesh/db-backups"


def cmd(args: list[str], timeout: int = 8) -> tuple[int, str]:
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout, check=False)
        return done.returncode, done.stdout[:100000]
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def systemd_unit(name: str) -> dict:
    command = ["systemctl", "--user", "show", name, "--no-pager"]
    for prop in READ_PROPS:
        command.extend(["-p", prop])
    rc, stdout = cmd(command)
    properties = dict(line.split("=", 1) for line in stdout.splitlines()
                      if "=" in line)
    start = properties.get("ExecStart", "")
    startpost = properties.get("ExecStartPost", "")
    stoppost = properties.get("ExecStopPost", "")
    release = re.search(r"rc(?:15|152)-[\w-]+", start)
    output = {
        "load": properties.get("LoadState", "UNKNOWN"),
        "active": properties.get("ActiveState", "UNKNOWN"),
        "enabled": properties.get("UnitFileState", "UNKNOWN"),
        "result": properties.get("Result", "UNKNOWN"),
        "main_exit": properties.get("ExecMainStatus", "UNKNOWN"),
    }
    if name.endswith(".service"):
        output["release"] = release.group(0) if release else "other"
        output["agent_wrapper"] = "meteor-status-agent.py" in start
        output["phone_watch_hook"] = "meteor-phone-watch.py" in startpost
        output["rc153_posthook"] = "after_analysis_with_backup.py" in stoppost
        output["night_phase"] = "sync" if "--phase sync" in start else (
            "analyze" if "--phase analyze" in start else "not_applicable")
    if rc != 0:
        output["read_error"] = True
    return output


def disk() -> dict:
    if DATA.is_symlink() or not DATA.is_dir() or not os.path.ismount(DATA):
        return {"verified": False, "reason": "NOT_A_REAL_MOUNT_OR_SYMLINK"}
    rc, out = cmd(["findmnt", "-n", "-o", "UUID,FSTYPE",
                   "--mountpoint", str(DATA)])
    fields = out.split()
    if rc or len(fields) != 2 or fields != [UUID, "ext4"]:
        return {"verified": False, "reason": "MOUNT_UUID_OR_FSTYPE_MISMATCH"}
    try:
        v = os.statvfs(DATA)
    except OSError:
        return {"verified": False, "reason": "STATVFS_UNAVAILABLE"}
    return {"verified": True, "fstype": "ext4", "uuid_matches": True,
            "free_gib": round(v.f_bavail * v.f_frsize / 1024**3, 2)}


def regular(path: Path, max_bytes: int) -> bool:
    try:
        s = path.lstat()
        return stat.S_ISREG(s.st_mode) and s.st_size <= max_bytes
    except OSError:
        return False


def recent_backup() -> dict:
    if BACKUPS.is_symlink() or not BACKUPS.is_dir():
        return {"state": "BACKUP_DIRECTORY_UNAVAILABLE"}
    try:
        files = [p for p in BACKUPS.glob("meteorhub-*.sqlite3")
                 if regular(p, 1024**3)]
        if not files:
            return {"state": "NO_BACKUP_SEEN"}
        last = max(files, key=lambda p: p.stat().st_mtime)
        return {"state": "FOUND", "name": last.name,
                "size_bytes": last.stat().st_size,
                "age_hours": round((dt.datetime.now().timestamp()
                                    - last.stat().st_mtime)/3600, 1)}
    except OSError:
        return {"state": "BACKUP_INSPECTION_FAILED"}


def last_report(mount_valid: bool) -> dict:
    if not mount_valid:
        return {"state": "SKIPPED_UNVERIFIED_HDD"}
    if REPORTS.is_symlink() or not REPORTS.is_dir():
        return {"state": "NO_REPORT_DIRECTORY"}
    try:
        files = [p for p in REPORTS.glob("morning_*.json")
                 if regular(p, 1024 * 1024)]
        if not files:
            return {"state": "NO_REPORT"}
        p = max(files, key=lambda f: f.name)
        with p.open("r", encoding="utf-8") as f:
            doc = json.load(f)
        if not isinstance(doc, dict):
            return {"state": "INVALID_REPORT"}
        return {"state": "READ_OK", "name": p.name,
                "outcome": str(doc.get("outcome", "UNKNOWN"))[:64]}
    except (OSError, ValueError):
        return {"state": "REPORT_READ_FAILED"}


def script(path: Path) -> dict:
    if not regular(path, 200000):
        return {"present": False}
    try:
        data = path.read_bytes()
    except OSError:
        return {"present": False}
    info = {"present": True, "sha256_first16": hashlib.sha256(data).hexdigest()[:16]}
    if path == WATCH:
        info["has_legacy_fixed_ips"] = all(s in data for s in
            (b"192.168.0.216", b"192.168.0.237", b"192.168.0.105"))
    return info


def main() -> None:
    units = {name: systemd_unit(name) for name in UNITS}
    storage = disk()
    agent = script(AGENT)
    watcher = script(WATCH)
    backup = recent_backup()
    report = last_report(storage["verified"])
    findings = []
    if not storage["verified"]:
        findings.append("BLOCKED:HDD_MOUNT_NOT_VERIFIED")
    elif storage["free_gib"] < 100:
        findings.append("WARNING:HDD_BELOW_100_GIB_RESERVE")
    for timer in ("meteormesh-status.timer", "meteormesh-morning.timer",
                  "meteormesh-capture.timer"):
        if units[timer]["active"] != "active":
            findings.append("WARNING:TIMER_NOT_ACTIVE:" + timer)
    if not agent["present"] or not units["meteormesh-status.service"]["agent_wrapper"]:
        findings.append("OPEN:STATUS_AGENT_EFFECTIVE_PATH_UNCONFIRMED")
    if not watcher["present"] or not units["meteormesh-status.service"]["phone_watch_hook"]:
        findings.append("OPEN:PHONE_WATCH_HOOK_UNCONFIRMED")
    if watcher.get("has_legacy_fixed_ips"):
        findings.append("OPEN:WATCHER_USES_HISTORICAL_STATIC_IPS")
    if not units["meteormesh-analyze.service"]["rc153_posthook"]:
        findings.append("OPEN:ANALYSIS_BACKUP_POSTHOOK_UNCONFIRMED")
    if backup["state"] != "FOUND":
        findings.append("WARNING:SSD_SQLITE_BACKUP_NOT_SEEN")
    if units["meteormesh-control-013.service"]["active"] != "active":
        findings.append("WARNING:CONTROL_8443_SERVICE_NOT_ACTIVE")
    findings.append("OPEN:EXTERNAL_PUSH_DELIVERY_NOT_YET_ACCEPTED")
    findings.append("OPEN:FULL_NIGHT_PHONE_ACCEPTANCE_PENDING")
    print(json.dumps({
        "schema": "meteormesh.ops-readonly-inventory.v1",
        "utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "units": units,
        "hdd": storage,
        "backup": backup,
        "morning_report": report,
        "agent": agent,
        "phone_watcher": watcher,
        "findings": findings,
        "read_only": True,
        "db_opened": False,
        "phones_contacted": False,
        "units_changed": False,
        "files_modified": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
