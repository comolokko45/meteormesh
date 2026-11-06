#!/usr/bin/env python3
"""Localhost-only, read-only MeteorMesh status monitor."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta, time as dt_time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time

RELEASE = Path(__file__).resolve().parent
INDEX = RELEASE / "index.html"
DATA = Path("/srv/meteor-data")
DB = DATA / "meteorhub.sqlite3"
STATE = Path.home() / ".local/state/meteormesh"
SUMMARY = Path.home() / ".local/share/meteormesh/releases/rc180-report-integrity-20261104/meteor-ops-summary.py"
SCOPE_FILE = Path.home() / ".local/share/meteormesh/staging/fullnight/night-scope-current.json"
OPS_ALERT_STATE = Path.home() / ".local/state/meteormesh/ops-alert-state-v1.json"
EXPECTED_UUID = "444a8a6d-090a-456c-8a9d-d99665c32574"
PORT = 8765
CACHE_SECONDS = 45
TZ = ZoneInfo("Europe/Istanbul")
_cache_lock = threading.Lock()
_cache = {"at": 0.0, "state": None}
_cpu_previous = None


def utc_now():
    return datetime.now(timezone.utc)


def iso_local(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            value = datetime.fromtimestamp(value / 1_000_000, timezone.utc)
        elif isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(TZ).isoformat(timespec="seconds")
    except (ValueError, TypeError, OSError):
        return None


def run_readonly(args, timeout=8):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def read_ops_summary():
    if not SUMMARY.is_file():
        raise RuntimeError("OPS_SUMMARY_NOT_FOUND")
    result = run_readonly(["/usr/bin/python3", "-B", str(SUMMARY), "--preview"], 25)
    if result is None:
        raise RuntimeError("OPS_SUMMARY_UNAVAILABLE")
    if result.returncode != 0:
        raise RuntimeError("OPS_SUMMARY_EXIT_" + str(result.returncode))
    value = json.loads(result.stdout)
    if value.get("state_written") is True or value.get("raw_videos_touched") is True:
        raise RuntimeError("OPS_PREVIEW_SAFETY_CONTRACT_FAILED")
    return value


def read_timers():
    result = run_readonly(["systemctl", "--user", "list-timers", "--all",
                           "--no-pager", "--output=json"], 5)
    if result is None or result.returncode != 0:
        return []
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    timers = []
    for row in rows if isinstance(rows, list) else []:
        name = str(row.get("unit", ""))
        if not name.startswith("meteormesh-"):
            continue
        timers.append({
            "unit": name,
            "activates": row.get("activates"),
            "next_local": iso_local(row.get("next")) if row.get("next") else None,
            "last_local": iso_local(row.get("last")) if row.get("last") else None,
            "scheduled": row.get("next") is not None,
        })
    return timers


def parse_analysis_execution(systemd_output, journal_output):
    """Summarize only the latest analyzer service run; never execute a job."""
    props = {}
    for line in systemd_output.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key] = value
    events = []
    for line in journal_output.splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(event, dict) and isinstance(event.get("MESSAGE"), str):
            events.append(event)
    starts = [i for i, event in enumerate(events)
              if event["MESSAGE"].startswith("Starting meteormesh-analyze.service")]
    run_events = events[starts[-1]:] if starts else []
    messages = [event["MESSAGE"] for event in run_events]

    source = None
    backup = {"state": "NOT_REPORTED", "name": None}
    for message in messages:
        for marker in ("ANALYSIS_QUEUE_NO_ACTIONABLE_SOURCE:", "ANALYSIS_SOURCE_READY:"):
            if marker in message:
                try:
                    source = json.loads(message.split(marker, 1)[1].strip())
                except (ValueError, TypeError):
                    source = {"state": "UNPARSEABLE"}
        if "MM153_DB_BACKUP: PASS" in message:
            backup["state"] = "PASS"
        elif "MM153_DB_BACKUP: ERROR" in message:
            backup["state"] = "ERROR"
        if message.startswith("BACKUP_PATH:"):
            backup["name"] = Path(message.partition(":")[2].strip()).name or None

    result = props.get("Result", "unknown")
    active = props.get("ActiveState") in {"active", "activating", "reloading"}
    try:
        no_actionable_source = bool(source and
            int(source.get("source_ready_jobs", -1)) == 0 and
            int(source.get("eligible_jobs", 0)) > 0)
    except (TypeError, ValueError):
        no_actionable_source = False
    if active:
        state = "RUNNING"
    elif result == "exec-condition":
        state = "SKIPPED_NO_SOURCE" if no_actionable_source else "SKIPPED_PRECONDITION"
    elif result == "success":
        state = "COMPLETED"
    elif result in {"exit-code", "signal", "core-dump", "timeout", "resources"}:
        state = "FAILED"
    else:
        state = "UNKNOWN"

    def local_stamp(event):
        try:
            return iso_local(int(event.get("__REALTIME_TIMESTAMP")))
        except (TypeError, ValueError):
            return None

    started = run_events[0] if run_events else None
    finished = run_events[-1] if run_events else None
    return {
        "state": state,
        "systemd_result": result,
        "condition_result": props.get("ConditionResult"),
        "started_local": local_stamp(started) if started else None,
        "last_event_local": local_stamp(finished) if finished else None,
        "source": ({key: source.get(key) for key in
                    ("night_id", "eligible_jobs", "source_ready_jobs",
                     "source_missing_jobs", "retired_jobs")}
                   if isinstance(source, dict) else None),
        "backup": backup,
    }


def read_analysis_execution():
    unit = run_readonly(["systemctl", "--user", "show", "meteormesh-analyze.service",
                         "-p", "ActiveState", "-p", "Result", "-p",
                         "ExecMainStatus", "-p", "ConditionResult", "--no-pager"], 4)
    journal = run_readonly(["journalctl", "--user", "-u", "meteormesh-analyze.service",
                            "-n", "100", "--no-pager", "-o", "json"], 5)
    if unit is None or journal is None or unit.returncode != 0 or journal.returncode != 0:
        return {"state": "UNAVAILABLE", "source": None,
                "backup": {"state": "UNKNOWN", "name": None}}
    return parse_analysis_execution(unit.stdout, journal.stdout)


def read_proc_stats():
    global _cpu_previous
    system = {"uptime_seconds": None, "cpu_percent": None,
              "ram_total_mib": None, "ram_available_mib": None,
              "swap_total_mib": None, "swap_free_mib": None,
              "cpu_temp_c": None}
    try:
        system["uptime_seconds"] = round(float(Path("/proc/uptime").read_text().split()[0]))
        fields = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                fields[key] = int(rest.strip().split()[0]) // 1024
        system["ram_total_mib"] = fields.get("MemTotal")
        system["ram_available_mib"] = fields.get("MemAvailable")
        system["swap_total_mib"] = fields.get("SwapTotal")
        system["swap_free_mib"] = fields.get("SwapFree")
        first = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        values = [int(x) for x in first]
        total = sum(values)
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        if _cpu_previous:
            dt = total - _cpu_previous[0]
            di = idle - _cpu_previous[1]
            if dt > 0:
                system["cpu_percent"] = round(max(0, min(100, 100 * (dt - di) / dt)), 1)
        _cpu_previous = (total, idle)
    except (OSError, ValueError, IndexError):
        pass
    try:
        readings = []
        for path in glob.glob("/sys/class/thermal/thermal_zone*"):
            typ = Path(path, "type").read_text().strip().lower()
            if any(token in typ for token in ("cpu", "package", "x86_pkg")):
                raw = int(Path(path, "temp").read_text().strip())
                temp = raw / 1000 if raw > 1000 else float(raw)
                if 0 < temp < 130:
                    readings.append(temp)
        if readings:
            system["cpu_temp_c"] = round(max(readings), 1)
    except (OSError, ValueError):
        pass
    return system


def read_system(summary):
    result = {"uptime_seconds": None, "cpu_percent": None, "cpu_temp_c": None,
              "ram_total_mib": None, "ram_available_mib": None,
              "swap_total_mib": None, "swap_free_mib": None,
              "root_free_gib": None, "hdd_free_gib": summary.get("hdd_free_gib"),
              "hdd_mount": False, "hdd_uuid": None, "hdd_uuid_expected": EXPECTED_UUID,
              "sqlite_state": "UNKNOWN", "sqlite_jobs": None, "sqlite_segments": None,
              "dispositions": {}, "deleted_hdd_files": 0, "deleted_hdd_bytes": 0,
              "smart_live_check": "NOT_CHECKED_BY_MONITOR",
              "known_reallocated_sectors": 446}
    result.update(read_proc_stats())
    try:
        result["root_free_gib"] = round(shutil.disk_usage("/").free / (1024 ** 3), 2)
    except OSError:
        pass
    findmnt = run_readonly(["findmnt", "-n", "-o", "UUID", "-T", str(DATA)], 4)
    if findmnt and findmnt.returncode == 0:
        result["hdd_uuid"] = findmnt.stdout.strip() or None
        result["hdd_mount"] = bool(result["hdd_uuid"])
    if result["hdd_uuid"]:
        result["hdd_uuid_expected"] = result["hdd_uuid"] == EXPECTED_UUID
    try:
        uri = "file:" + str(DB) + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=2)
        con.execute("PRAGMA query_only=ON")
        result["sqlite_state"] = con.execute("PRAGMA quick_check").fetchone()[0].upper()
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "jobs" in tables:
            result["sqlite_jobs"] = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        if "segments" in tables:
            result["sqlite_segments"] = con.execute(
                "SELECT COUNT(*) FROM segments").fetchone()[0]
        if "source_dispositions" in tables:
            for disposition, count in con.execute(
                    "SELECT disposition, COUNT(*) FROM source_dispositions GROUP BY disposition"):
                result["dispositions"][str(disposition)] = int(count)
            row = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(s.size_bytes),0) "
                "FROM source_dispositions d LEFT JOIN segments s "
                "ON s.device_id=d.device_id AND s.segment_id=d.segment_id "
                "WHERE d.deleted_utc IS NOT NULL").fetchone()
            result["deleted_hdd_files"] = int(row[0])
            result["deleted_hdd_bytes"] = int(row[1])
        con.close()
    except (OSError, sqlite3.Error):
        result["sqlite_state"] = "UNAVAILABLE"
    return result


def capture_window(now=None):
    instant = now or utc_now()
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local_now = instant.astimezone(TZ)
    night_day = local_now.date() if local_now.hour >= 19 else local_now.date() - timedelta(days=1)
    start_local = datetime.combine(night_day, dt_time(19, 0), tzinfo=TZ)
    end_local = datetime.combine(night_day + timedelta(days=1), dt_time(5, 0), tzinfo=TZ)
    return {
        "night_id": night_day.strftime("%Y%m%d") + "-night",
        "start_local": start_local,
        "end_local": end_local,
        "start_utc": start_local.astimezone(timezone.utc),
        "end_utc": end_local.astimezone(timezone.utc),
        "observed_local": local_now,
    }


def read_analysis_scope():
    try:
        value = json.loads(SCOPE_FILE.read_text(encoding="utf-8"))
        return {"state": "READ_OK", "night_id": value.get("night_id"),
                "status": value.get("status"),
                "automatic_run_allowed": value.get("automatic_run_allowed"),
                "ingest_scope_enforced": value.get("ingest_scope_enforced"),
                "queue_scope_enforced": value.get("queue_scope_enforced"),
                "retention_auto_delete": value.get("retention_auto_delete")}
    except (OSError, ValueError, TypeError):
        return {"state": "UNAVAILABLE", "night_id": None}


def read_ops_alert_state():
    try:
        value = json.loads(OPS_ALERT_STATE.read_text(encoding="utf-8"))
        active = value.get("active")
        return {"state": "READ_OK", "schema": value.get("schema"),
                "active": active if isinstance(active, list) else [],
                "last_digest_day": value.get("last_digest_day"),
                "history_available": False}
    except (OSError, ValueError, TypeError):
        return {"state": "UNAVAILABLE", "active": [], "history_available": False}


def _segment_start(segment_id):
    found = re.search(r"(\d{8}T\d{6}Z)", str(segment_id))
    if not found:
        return None
    try:
        return datetime.strptime(found.group(1), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def current_segments(now=None):
    window = capture_window(now)
    night = window["night_id"]
    start_utc = window["start_utc"]
    end_utc = window["end_utc"]
    observed = window["observed_local"].astimezone(timezone.utc)
    try:
        con = sqlite3.connect("file:" + str(DB) + "?mode=ro", uri=True, timeout=2)
        con.execute("PRAGMA query_only=ON")
        rows = con.execute(
            "SELECT device_id, segment_id, size_bytes, state, verified_utc, ffprobe_json "
            "FROM segments WHERE verified_utc >= ? AND verified_utc <= ? "
            "ORDER BY verified_utc", (start_utc.isoformat(timespec="seconds"),
                                      observed.isoformat(timespec="seconds"))).fetchall()
        job_rows = con.execute(
            "SELECT j.device_id, j.segment_id, j.state, COUNT(*) "
            "FROM jobs j JOIN segments s ON s.device_id=j.device_id AND s.segment_id=j.segment_id "
            "WHERE s.verified_utc >= ? AND s.verified_utc <= ? "
            "GROUP BY j.device_id, j.segment_id, j.state",
            (start_utc.isoformat(timespec="seconds"),
             observed.isoformat(timespec="seconds"))).fetchall()
        con.close()
    except (OSError, sqlite3.Error):
        return {"night_id": night, "devices": {}, "gaps": [], "state": "UNAVAILABLE",
                "analysis_queue": {}, "window_start_local": iso_local(window["start_local"]),
                "window_end_local": iso_local(window["end_local"])}

    devices = {device: {"verified": 0, "bytes": 0, "segments": [], "analysis": {
                            "WAITING": 0, "RUNNING": 0, "SUCCEEDED": 0,
                            "FAILED": 0, "NO_JOB": 0}}
               for device in ("cam-ruby", "cam-surya", "cam-viva")}
    gaps = []
    retained = set()
    for device_id, segment_id, size, state, verified, probe in rows:
        stamp = _segment_start(segment_id)
        if stamp is None or not (start_utc <= stamp < end_utc):
            continue
        key = (str(device_id), str(segment_id))
        retained.add(key)
        item = devices.setdefault(device_id, {"verified": 0, "bytes": 0,
                                               "segments": [], "analysis": {
                                                   "WAITING": 0, "RUNNING": 0,
                                                   "SUCCEEDED": 0, "FAILED": 0,
                                                   "NO_JOB": 0}})
        item["verified"] += 1
        item["bytes"] += int(size or 0)
        duration = None
        try:
            decoded = json.loads(probe) if isinstance(probe, str) else probe
            duration = float(decoded.get("format", {}).get("duration"))
        except (ValueError, TypeError, AttributeError):
            pass
        item["segments"].append({
            "segment_id": str(segment_id), "state": str(state),
            "verified_local": iso_local(verified), "start_local": iso_local(stamp),
            "duration_seconds": round(duration, 2) if duration is not None else None,
            "size_bytes": int(size or 0), "analysis_counts": {},
            "analysis_state": "NO_JOB"})

    lookup = {(d, s): (item, segment) for d, item in devices.items()
              for segment in item["segments"] for s in [segment["segment_id"]]}
    analysis_queue = {"WAITING": 0, "RUNNING": 0, "SUCCEEDED": 0, "FAILED": 0,
                      "NO_JOB_SEGMENTS": 0}
    job_counts = {}
    for device_id, segment_id, state, count in job_rows:
        key = (str(device_id), str(segment_id))
        if key not in retained:
            continue
        job_counts.setdefault(key, {})[str(state)] = int(count)
        analysis_queue[str(state)] = analysis_queue.get(str(state), 0) + int(count)
    for key, (item, segment) in lookup.items():
        counts = job_counts.get(key, {})
        segment["analysis_counts"] = counts
        if not counts:
            segment["analysis_state"] = "NO_JOB"
            item["analysis"]["NO_JOB"] += 1
            analysis_queue["NO_JOB_SEGMENTS"] += 1
            continue
        state = ("FAILED" if counts.get("FAILED") else
                 "RUNNING" if counts.get("RUNNING") else
                 "WAITING" if counts.get("WAITING") else "SUCCEEDED")
        segment["analysis_state"] = state
        item["analysis"][state] += 1

    for device, item in devices.items():
        item["segments"].sort(key=lambda s: s["start_local"] or "")
        starts = []
        for segment in item["segments"]:
            stamp = _segment_start(segment["segment_id"])
            if stamp:
                starts.append((stamp, segment["duration_seconds"]))
        starts.sort(key=lambda row: row[0])
        if starts:
            initial = (starts[0][0] - start_utc).total_seconds()
            if initial > 360:
                gaps.append({"device_id": device, "from_local": iso_local(start_utc),
                             "to_local": iso_local(starts[0][0]),
                             "interval_seconds": round(initial, 1),
                             "missing_seconds": round(initial, 1),
                             "kind": "WINDOW_START_DELAY"})
        for (a, duration), (b, _) in zip(starts, starts[1:]):
            interval = (b - a).total_seconds()
            missing = max(0.0, interval - (duration if duration is not None else 300.0))
            if interval > 360:
                gaps.append({"device_id": device, "from_local": iso_local(a),
                             "to_local": iso_local(b), "interval_seconds": round(interval, 1),
                             "missing_seconds": round(missing, 1), "kind": "SEGMENT_INTERVAL"})
        if starts and observed < end_utc:
            last_start, last_duration = starts[-1]
            last_end = last_start + timedelta(seconds=last_duration or 300)
            trailing = (observed - last_end).total_seconds()
            if trailing > 360:
                gaps.append({"device_id": device, "from_local": iso_local(last_end),
                             "to_local": iso_local(observed),
                             "interval_seconds": round(trailing, 1),
                             "missing_seconds": round(trailing, 1),
                             "kind": "TRAILING_UNVERIFIED"})

    elapsed_end = min(observed, end_utc)
    expected = max(0, int((elapsed_end - start_utc).total_seconds() // 300))
    for item in devices.values():
        item["expected_completed_segments"] = expected
        item["not_yet_verified_vs_expected"] = max(0, expected - item["verified"])
    return {"night_id": night, "devices": devices, "gaps": gaps,
            "state": "READ_OK", "gap_threshold_seconds": 360,
            "analysis_queue": analysis_queue,
            "window_start_local": iso_local(window["start_local"]),
            "window_end_local": iso_local(window["end_local"]),
            "observed_until_local": iso_local(window["observed_local"]),
            "expected_completed_segments": expected}


def phone_view(summary):
    hb = summary.get("phone_heartbeat") or {}
    source = hb.get("devices") or {}
    pretty = {"viva": "Viva", "ruby": "Ruby", "surya": "Surya"}
    result = []
    for key in ("ruby", "surya", "viva"):
        raw = source.get(key) or {}
        http = raw.get("http_api") or {}
        control = raw.get("control_013") or {}
        adb = raw.get("adb") or {}
        rec = raw.get("recording") or {}
        storage = raw.get("storage") or {}
        result.append({
            "name": pretty[key], "code": key,
            "model": adb.get("model"),
            "ip": http.get("ip"), "api_port": http.get("port"),
            "app_version": control.get("app_version"),
            "http": http.get("status", "UNKNOWN"),
            "http_seen": http.get("last_success_utc"),
            "http_error": http.get("last_error"),
            "control": control.get("status", "UNKNOWN"),
            "control_seen": control.get("last_success_utc"),
            "control_age_seconds": control.get("age_seconds"),
            "control_ttl_seconds": control.get("heartbeat_ttl_seconds"),
            "adb": adb.get("status", "UNKNOWN"),
            "adb_seen": adb.get("last_success_utc"),
            "camera": control.get("camera_state") or rec.get("status", "UNKNOWN"),
            "camera_ready": rec.get("remote_camera_ready"),
            "segment_id": rec.get("active_segment_id"),
            "last_mp4": rec.get("last_completed_mp4_utc_ms"),
            "segment_target_ms": rec.get("recording_segment_target_ms"),
            "ready_segments": rec.get("ready_segments", control.get("ready_segments")),
            "rotation_mode": rec.get("rotation_mode"),
            "boundary_verified": rec.get("segment_boundary_verified"),
            "free_gib": storage.get("free_gib"),
            "reserve_gib": round((storage.get("protected_reserve_bytes") or 0) / 1024**3, 2),
            "reserve_ok": storage.get("reserve_ok"),
            "storage_status": storage.get("status", "UNKNOWN"),
        })
    return result


def read_recent_reports():
    folder = DATA / "morning-reports"
    try:
        files = sorted(folder.glob("morning_*.json"), key=lambda p: p.stat().st_mtime,
                       reverse=True)[:30]
    except OSError:
        return {"state": "UNAVAILABLE", "samples": 0, "nights_7d": 0,
                "nights_30d": 0, "items": []}
    items = []
    unique_days_7 = set()
    unique_days_30 = set()
    now = time.time()
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            night = str(data.get("night_id") or "")
            stamp = data.get("finished_utc") or data.get("created_utc")
            item = {"name": path.name, "night_id": night or None,
                    "finished_local": iso_local(stamp),
                    "outcome": data.get("outcome"),
                    "jobs_enqueued": data.get("jobs_enqueued"),
                    "sync_good": data.get("sync_good")}
            items.append(item)
            age_days = (now - path.stat().st_mtime) / 86400
            if night and age_days <= 7:
                unique_days_7.add(night)
            if night and age_days <= 30:
                unique_days_30.add(night)
        except (OSError, ValueError, TypeError):
            continue
    return {"state": "READ_OK", "samples": len(items),
            "nights_7d": len(unique_days_7), "nights_30d": len(unique_days_30),
            "items": items[:12]}


def build_state():
    created = utc_now().isoformat(timespec="seconds")
    try:
        summary = read_ops_summary()
        source_error = None
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        summary = {}
        source_error = str(exc)[:100]
    assessment = summary.get("assessment") or {}
    hb = summary.get("phone_heartbeat") or {}
    hb_counts = hb.get("counts") or {}
    now_utc = utc_now()
    per_device_seen = {}
    for device, value in (hb.get("devices") or {}).items():
        channel_times = []
        for channel in ("http_api", "control_013"):
            stamp = (value.get(channel) or {}).get("last_success_utc")
            if stamp:
                try:
                    channel_times.append(datetime.fromisoformat(
                        str(stamp).replace("Z", "+00:00")))
                except ValueError:
                    pass
        if channel_times:
            last_seen = max(channel_times)
            per_device_seen[device] = {
                "last_seen_utc": last_seen.isoformat(timespec="seconds"),
                "age_seconds": max(0, int((now_utc - last_seen).total_seconds())),
            }
    heartbeat_time = min((v["last_seen_utc"] for v in per_device_seen.values()), default=None)
    heartbeat_age = max((v["age_seconds"] for v in per_device_seen.values()), default=None)
    stale = (source_error is not None or heartbeat_age is None or heartbeat_age > 600
             or len(per_device_seen) < 3)
    level = "VERİ GÜNCEL DEĞİL" if stale else str(assessment.get("level") or "UNKNOWN")
    night = summary.get("night_status") or {}
    detector = night.get("detector_summary") or {}
    cleanup = night.get("hdd_negative_cleanup") or {}
    segments = current_segments(now_utc)
    analysis_scope = read_analysis_scope()
    ops_alerts = read_ops_alert_state()
    capture_night_id = segments.get("night_id")
    report_night_id = night.get("night_id")
    scope_matches_capture = (analysis_scope.get("state") == "READ_OK" and
                             analysis_scope.get("night_id") == capture_night_id)
    report_matches_capture = report_night_id == capture_night_id
    capture_queue = segments.get("analysis_queue") or {}
    if not scope_matches_capture:
        try:
            scope_due = datetime.fromisoformat(segments["window_end_local"]) + timedelta(minutes=30)
            scope_pending = now_utc.astimezone(TZ) < scope_due + timedelta(minutes=15)
        except (KeyError, TypeError, ValueError):
            scope_due = None
            scope_pending = False
        capture_analysis_state = ("AWAITING_PREDawn_SCOPE" if scope_pending
                                  else "SCOPE_ROTATION_LATE")
    elif capture_queue.get("RUNNING", 0):
        scope_due = None
        capture_analysis_state = "IN_PROGRESS"
    elif capture_queue.get("WAITING", 0):
        scope_due = None
        capture_analysis_state = "QUEUED"
    elif capture_queue.get("SUCCEEDED", 0) or capture_queue.get("FAILED", 0):
        scope_due = None
        capture_analysis_state = "TERMINAL_JOBS_PRESENT"
    else:
        scope_due = None
        capture_analysis_state = "NO_ANALYSIS_JOBS"
    if scope_matches_capture:
        scope_due = None
    if report_matches_capture:
        capture_detector = detector
    else:
        capture_detector = {"state": "NO_MATCHING_CAPTURE_NIGHT_REPORT",
                            "analysis_complete": False,
                            "detector_result_items_unreviewed": None,
                            "verified_result_files": 0,
                            "invalid_or_missing_result_files": 0,
                            "result_item_semantics": "NO_RESULT_IS_NOT_NO_METEOR"}
    return {
        "schema": "meteormesh.monitor.v1",
        "generated_utc": created,
        "generated_local": iso_local(created),
        "source_error": source_error,
        "source": "RC180 salt-okunur OpsJournal özeti (--preview)",
        "freshness": {"state": "STALE" if stale else "CURRENT",
                      "http_last_success_utc": heartbeat_time,
                      "heartbeat_age_seconds": heartbeat_age,
                      "per_device": per_device_seen,
                      "max_age_seconds": 600,
                      "phone_source_state": hb.get("state", "UNKNOWN")},
        "overall": {"level": level,
                    "codes": assessment.get("codes") or [],
                    "historical_failed_jobs": assessment.get("historical_failed_jobs"),
                    "night_fully_accepted": assessment.get("night_fully_accepted", False)},
        "night": {
            "night_id": night.get("night_id"),
            "reported_night_id": report_night_id,
            "capture_night_id": capture_night_id,
            "analysis_scope_file": analysis_scope,
            "scope_matches_capture": scope_matches_capture,
            "report_matches_capture": report_matches_capture,
            "capture_analysis": {"state": capture_analysis_state,
                                 "queue": capture_queue,
                                 "scope_rotation_due_local": iso_local(scope_due)},
            "outcome": night.get("outcome"),
            "finished_utc": night.get("finished_utc"),
            "queue": night.get("night_queue") or {},
            "carryover": night.get("carryover_queue") or {},
            "device_coverage": night.get("device_coverage") or {},
            "jobs_enqueued": night.get("jobs_enqueued"),
            "sync_good": night.get("sync_good"),
            "sync_pending_or_failed": night.get("sync_pending_or_failed"),
            "historical_queue_excluded": night.get("historical_queue_excluded"),
            "detector": capture_detector,
            "last_report_detector": detector,
            "phone_purge": night.get("phone_purge") or {},
            "hdd_cleanup": cleanup,
        },
        "phones": phone_view(summary),
        "segments": segments,
        "system": read_system(summary),
        "timers": read_timers(),
        "analysis_execution": read_analysis_execution(),
        "auto_recovery": summary.get("auto_recovery") or {},
        "ops_alerts": ops_alerts,
        "backup": summary.get("backup") or {},
        "network_readiness": summary.get("network_readiness") or {},
        "reports": read_recent_reports(),
        "preview_contract": {
            "state_written": summary.get("state_written", False),
            "raw_videos_touched": summary.get("raw_videos_touched", False),
            "notification_sent": summary.get("notification_sent", False),
            "external_phones_contacted": summary.get("external_phones_contacted", False),
        },
        "acceptance": [
            {"area": "software", "name": "MeteorNode 0.13.6 üç cihazda",
             "status": "PASS" if len([p for p in phone_view(summary)
                                      if str(p.get("app_version") or "").startswith("0.13.6")]) == 3 else "PARTIAL",
             "evidence": "En son Control-013/telefon özeti"},
            {"area": "software", "name": "Timed-muxer segment kısa kabulü",
             "status": "PASS", "evidence": "4 Kasım RC177 kısa gerçek cihaz kabul kaydı; tam gece kanıtı değildir"},
            {"area": "software", "name": "HTTP, Control-013 ve ADB ayrı sınıflandırma",
             "status": "PASS", "evidence": "Canlı heartbeat özeti; ADB kopukluğu MeteorNode OFFLINE yapılmıyor"},
            {"area": "software", "name": "Yeni gece ile tarihsel kuyruk ayrımı",
             "status": "PASS" if night.get("historical_queue_excluded") is True else "PARTIAL",
             "evidence": "Son kapsamlı morning report"},
            {"area": "software", "name": "Negatif MP4 tombstone ve silme güvenliği",
             "status": "PARTIAL", "evidence": "Tombstone var; analiz sonrası gerçek silme dry-run ve açık pilot onayı bekliyor"},
            {"area": "software", "name": "Bilgi Monitörü localhost salt-okunur",
             "status": "PARTIAL", "evidence": "RC188 son analyzer/skip nedeni ve backup fişini de gösterir; Tailscale/Android erişim kabulü ve diğer ek telemetri bekliyor"},
            {"area": "software", "name": "Rollback ve sürümlü G62 yayın",
             "status": "PASS", "evidence": "RC188 release canlı; RC187 monitor kaynağı/ünitesi rollback altında"},
            {"area": "software", "name": "G62 SMART güncel ölçüm",
             "status": "PARTIAL", "evidence": "Monitör sürekli SMART taraması yapmıyor; önceki 446 reallocated sektör kaydı var"},
            {"area": "operations", "name": "Doğal 19:00 başlangıç ve 05:00 tam gece",
             "status": "PARTIAL", "evidence": "Bu gece aktif kayıt gözleniyor; 05:00 kapanış ve kapsam kabulü bekliyor"},
            {"area": "operations", "name": "Üç cihazın gerçek segment ilerlemesi",
             "status": "PARTIAL", "evidence": "Canlı durum ve doğrulanmış ingest var; tüm gece sürekliliği bekliyor"},
            {"area": "operations", "name": "G62 aktarımı ve SQLite/rapor eşleşmesi",
             "status": "PARTIAL", "evidence": "Son sync 3/3 başarılı; doğal gece zinciri henüz sürüyor"},
            {"area": "operations", "name": "05:30 doğal scope sync ve analiz",
             "status": "BLOCKED", "evidence": "Bir sonraki tetik 5 Kasım 05:30 TRT"},
            {"area": "operations", "name": "09:00 tamamlayıcı aktarım ve tek rapor",
             "status": "BLOCKED", "evidence": "Bir sonraki tetik 5 Kasım 09:00 TRT"},
            {"area": "operations", "name": "Negatif MP4 pilotu ve otomatik silme",
             "status": "BLOCKED", "evidence": "Dry-run-only; gerçek pilot ve ayrı otomasyon onayı gerekli"},
            {"area": "operations", "name": "G62 reboot sonrası uçtan uca devam",
             "status": "PARTIAL", "evidence": "Persistent timer/ağ hazır kapısı var; kontrollü canlı reboot kabulü yok"},
            {"area": "operations", "name": "Kesilen .part aktarımının gerçek resume kabulü",
             "status": "PARTIAL", "evidence": "Range resume altyapısı var; bu kabul turunda canlı kesinti uygulanmadı"},
            {"area": "operations", "name": "Çok gecelik güvenilirlik",
             "status": "BLOCKED", "evidence": "Bir veya daha fazla doğal gece gözlem verisi henüz birikmedi"},
            {"area": "operations", "name": "Güvenli dış kritik bildirim teslimi",
             "status": "PARTIAL", "evidence": "OpsJournal yerel; dış push teslimi yapılandırılmamış"},
        ],
        "source_state": "READ_OK" if source_error is None else "UNAVAILABLE",
    }


def get_state():
    now = time.monotonic()
    with _cache_lock:
        if _cache["state"] is None or now - _cache["at"] >= CACHE_SECONDS:
            state = build_state()
            _cache["state"] = state
            _cache["at"] = now
        return _cache["state"]


class Handler(BaseHTTPRequestHandler):
    server_version = "MeteorMeshMonitor/RC188"
    sys_version = ""

    def log_message(self, fmt, *args):
        # Do not put request query strings or application data into logs.
        print("MONITOR_HTTP: " + str(args[0])[:60], flush=True)

    def _headers(self, status, content_type, length):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
                         "img-src 'self' data:; object-src 'none'; base-uri 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    def do_GET(self):
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            self._headers(403, "text/plain; charset=utf-8", 9)
            self.wfile.write(b"Forbidden")
            return
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            try:
                body = json.dumps(get_state(), ensure_ascii=False).encode("utf-8")
            except Exception as exc:
                body = json.dumps({"source_state": "ERROR",
                                   "error": type(exc).__name__}).encode("utf-8")
            self._headers(200, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)
            return
        if path == "/healthz":
            body = b"ok"
            self._headers(200, "text/plain; charset=utf-8", len(body))
            self.wfile.write(body)
            return
        if path in ("/", "/index.html"):
            try:
                body = INDEX.read_bytes()
            except OSError:
                body = b"MeteorMesh Monitor UI is unavailable"
                self._headers(503, "text/plain; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            self._headers(200, "text/html; charset=utf-8", len(body))
            self.wfile.write(body)
            return
        body = b"Not found"
        self._headers(404, "text/plain; charset=utf-8", len(body))
        self.wfile.write(body)

    def do_POST(self):
        body = b"Read-only monitor"
        self._headers(405, "text/plain; charset=utf-8", len(body))
        self.wfile.write(body)

    do_PUT = do_POST
    do_DELETE = do_POST


def main():
    if not INDEX.is_file():
        raise SystemExit("monitor index missing")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    print("METEORMESH_MONITOR_LISTENING: 127.0.0.1:" + str(PORT), flush=True)
    try:
        server.serve_forever(poll_interval=1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
