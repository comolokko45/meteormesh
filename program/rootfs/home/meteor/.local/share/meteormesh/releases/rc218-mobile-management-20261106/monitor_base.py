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
import stat
import subprocess
import threading
import time

RELEASE = Path(__file__).resolve().parent
INDEX = RELEASE / "index.html"
DATA = Path("/srv/meteor-data")
DB = DATA / "meteorhub.sqlite3"
STATE = Path.home() / ".local/state/meteormesh"
SUMMARY = Path.home() / ".local/lib/meteormesh/meteor-ops-summary.py"
SCOPE_FILE = Path.home() / ".local/share/meteormesh/staging/fullnight/night-scope-current.json"
OPS_ALERT_STATE = Path.home() / ".local/state/meteormesh/ops-alert-state-v1.json"
RETENTION_POLICY = STATE / "negative-video-retention-enabled.json"
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


def read_retention_policy():
    """Read the live, fail-closed negative-video retention policy marker."""
    try:
        if RETENTION_POLICY.is_symlink() or not RETENTION_POLICY.is_file():
            raise ValueError("unsafe or missing policy")
        value = json.loads(RETENTION_POLICY.read_text(encoding="utf-8"))
        if value.get("schema") != "meteormesh.negative-retention-activation.v1":
            raise ValueError("unexpected policy schema")
        enabled = value.get("auto_delete_enabled") is True
        enabled_utc = value.get("auto_delete_enabled_utc") if enabled else None
        if enabled_utc is not None and not isinstance(enabled_utc, str):
            enabled_utc = None
        return {"auto_delete_enabled": enabled,
                "auto_delete_enabled_utc": enabled_utc,
                "policy_state": "READ_OK" if enabled else "DISABLED",
                "state": "ENABLED_EXECUTION_GATED" if enabled else "DISABLED_BY_POLICY",
                "approval_required_before_enabling": False if enabled else None}
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return {"auto_delete_enabled": False,
                "auto_delete_enabled_utc": None,
                "policy_state": "UNAVAILABLE"}


def _parse_utc(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def retention_acceptance(cleanup, dispositions):
    """Separate proven bounded cleanup implementation from live-run acceptance."""
    enabled = cleanup.get("auto_delete_enabled") is True
    receipt_count = int((dispositions or {}).get("NO_CANDIDATE_DELETED") or 0)
    try:
        cap = int(cleanup.get("max_deletes_per_analysis_hook") or 0)
    except (TypeError, ValueError):
        cap = 0
    bounded_policy = 1 <= cap <= 5
    software_pass = enabled and bounded_policy and receipt_count > 0

    activated = _parse_utc(cleanup.get("auto_delete_enabled_utc"))
    last_deleted = _parse_utc(cleanup.get("last_deleted_utc"))
    post_activation_delete = (activated is not None and last_deleted is not None
                              and last_deleted >= activated)
    if enabled and post_activation_delete:
        operations_status = "PASS"
        operations_evidence = ("Etkinleştirme sonrası doğal analiz kancası HDD MP4 temizledi; "
                               f"son fiş {last_deleted.isoformat(timespec='seconds')}.")
    elif enabled:
        operations_status = "PARTIAL"
        operations_evidence = ("Sınırlı otomatik politika etkin; önceki tek-dosya pilotu mevcut. "
                               "Etkinleştirme sonrası doğal analiz kancası henüz silme fişi üretmedi.")
    else:
        operations_status = "BLOCKED"
        operations_evidence = ("Canlı politika devre dışı veya okunamadı; otomatik HDD temizliği "
                               "çalışmıyor.")
    software_status = "PASS" if software_pass else "PARTIAL"
    software_evidence = (
        f"Sınırlı kap (en fazla {cap}/analiz kancası) ve NO_CANDIDATE_DELETED fişi="
        f"{receipt_count} (tek-dosya pilotu dahil); etkinleştirme sonrası doğal çalışma ayrıca izleniyor."
        if software_pass else
        f"Sınırlı politika/negatif-silme fişi kanıtı tamam değil (etkin={enabled}, kap={cap}, fiş={receipt_count}).")
    return {"software_status": software_status,
            "software_evidence": software_evidence,
            "operations_status": operations_status,
            "operations_evidence": operations_evidence,
            "post_activation_delete_confirmed": post_activation_delete}


def merge_local_alerts(level, codes, ops_alerts):
    """Do not display OK when a deduplicated local incident remains open."""
    merged = list(codes) if isinstance(codes, list) else []
    if not isinstance(ops_alerts, dict) or ops_alerts.get('state') != 'READ_OK':
        if 'OPS_ALERT_STATE_UNAVAILABLE' not in merged:
            merged.append('OPS_ALERT_STATE_UNAVAILABLE')
        return ('WARNING' if level == 'OK' else level), merged
    active = ops_alerts.get('active', []) if isinstance(ops_alerts, dict) and ops_alerts.get('state') == 'READ_OK' else []
    if isinstance(active, list):
        for code in active:
            if isinstance(code, str) and code and code not in merged:
                merged.append(code)
        if active and level == 'OK':
            level = 'WARNING'
    return level, merged


def read_timers():
    result = run_readonly(["systemctl", "--user", "list-timers", "--all",
                           "--no-pager", "--output=json"], 5)
    if result is None or result.returncode != 0:
        return []
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    by_name = {str(row.get("unit")): row for row in rows
               if isinstance(row, dict) and str(row.get("unit", "")).startswith("meteormesh-")}
    known = {
        "meteormesh-status.timer", "meteormesh-morning.timer",
        "meteormesh-capture.timer", "meteormesh-hourly-pull.timer",
        "meteormesh-midnight.timer", "meteormesh-analysis-rounds.timer",
        "meteormesh-predawn.timer",
    }
    timers = []
    for name in sorted(known | set(by_name)):
        row = by_name.get(name, {})
        unit = read_unit_state(name)
        if not row and unit.get("load_state") in {"not-found", "UNAVAILABLE"}:
            continue
        activates = row.get("activates") or (name[:-6] + ".service")
        target = read_unit_state(activates)
        next_value = row.get("next")
        unit_file_state = unit.get("unit_file_state", "unknown")
        if name == "meteormesh-morning.timer" and unit_file_state in {"disabled", "masked"}:
            timer_state = "POLICY_DISABLED"
        elif target.get("state") in {"active", "activating", "reloading"}:
            timer_state = "RUNNING_SCHEDULED" if next_value else "RUNNING"
        elif next_value:
            timer_state = "SCHEDULED"
        elif unit.get("state") in {"active", "activating", "reloading"}:
            timer_state = "ACTIVE_NO_NEXT"
        elif unit_file_state in {"disabled", "masked"}:
            timer_state = "DISABLED"
        else:
            timer_state = "INACTIVE"
        timers.append({
            "unit": name,
            "activates": activates,
            "next_local": iso_local(next_value) if next_value else None,
            "last_local": iso_local(row.get("last")) if row.get("last") else None,
            "scheduled": next_value is not None,
            "active_state": unit.get("state"),
            "unit_file_state": unit_file_state,
            "activates_state": target.get("state"),
            "timer_state": timer_state,
        })
    return timers

def read_unit_state(unit):
    result = run_readonly(["systemctl", "--user", "show", unit,
                           "-p", "ActiveState", "-p", "SubState", "-p",
                           "Result", "-p", "ExecMainStatus", "-p", "LoadState",
                           "-p", "UnitFileState", "--no-pager"], 4)
    if result is None or result.returncode != 0:
        return {"state": "UNAVAILABLE", "result": "unknown", "exit_status": None}
    values = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key] = value
    try:
        exit_status = int(values["ExecMainStatus"])
    except (KeyError, ValueError):
        exit_status = None
    return {"state": values.get("ActiveState", "unknown"),
            "substate": values.get("SubState", "unknown"),
            "result": values.get("Result", "unknown"),
            "exit_status": exit_status,
            "load_state": values.get("LoadState", "unknown"),
            "unit_file_state": values.get("UnitFileState", "unknown")}


def natural_timer_gates(timers, services, analysis_execution, reports,
                        capture_night, now=None, capture_segments=None,
                        scope_matches_capture=False, report_matches_capture=False,
                        carryover=None, detector=None):
    """Accept the 05:30 kickoff and queue drain; 09:00 is intentionally optional."""
    instant = now or utc_now()
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local_now = instant.astimezone(TZ)
    timer_map = {item.get("unit"): item for item in timers}
    predawn_timer = timer_map.get("meteormesh-predawn.timer", {})
    predawn_service = services.get("predawn", {})
    sync_service = services.get("morning", {})
    analysis_execution = analysis_execution or {}
    source = analysis_execution.get("source") or {}
    queue = (capture_segments or {}).get("analysis_queue") or {}
    queue_scope = (capture_segments or {}).get("night_id")
    carryover = carryover or {}
    detector = detector or {}

    try:
        capture_day = datetime.strptime(capture_night, "%Y%m%d-night").date()
        due = datetime.combine(capture_day + timedelta(days=1), dt_time(5, 30), TZ)
    except (TypeError, ValueError):
        due = None
    if due is None:
        return {
            "predawn": {"status": "BLOCKED", "evidence": "Gece kimliği doğrulanamadı; 05:30 işi eşleştirilemiyor."},
            "analysis_drain": {"status": "BLOCKED", "evidence": "Gece kimliği doğrulanamadı; analiz kuyruğu eşleştirilemiyor."},
        }
    if local_now < due:
        evidence = "Bu kayıt gecesinin 05:30 başlangıcı henüz gelmedi: " + due.isoformat()
        return {
            "predawn": {"status": "PARTIAL", "evidence": evidence},
            "analysis_drain": {"status": "PARTIAL", "evidence": "05:30 sonrası analiz kuyruğu bekleniyor: " + due.isoformat()},
        }

    def local_stamp(value):
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                return None
            return stamp.astimezone(TZ)
        except (ValueError, TypeError):
            return None

    last_trigger = local_stamp(predawn_timer.get("last_local"))
    predawn_seen = last_trigger is not None and last_trigger >= due
    sync_reports = (reports or {}).get("today_sync_reports")
    if sync_reports is None:
        sync_reports = [item for item in (reports or {}).get("items", [])
                        if item.get("phase") == "sync"]
    matching_sync = []
    for item in sync_reports:
        finished = local_stamp(item.get("finished_local"))
        if (item.get("night_id") == capture_night and item.get("phase") == "sync"
                and finished is not None and finished >= due):
            matching_sync.append(item)
    matching_sync.sort(key=lambda item: str(item.get("finished_local") or ""))

    analysis_started = local_stamp(analysis_execution.get("started_local"))
    analysis_state = analysis_execution.get("state")
    analysis_for_capture = (
        analysis_started is not None and analysis_started >= due
        and source.get("night_id") == capture_night
        and analysis_state in {"RUNNING", "COMPLETED", "SKIPPED_NO_SOURCE"}
    )

    if not predawn_seen:
        predawn = {"status": "BLOCKED",
                   "evidence": "Bu gece için 05:30 predawn timer kanıtı yok; sonraki: "
                               + str(predawn_timer.get("next_local") or "bilinmiyor")}
    elif predawn_service.get("state") in {"active", "activating", "reloading"} or sync_service.get("state") in {"active", "activating", "reloading"}:
        predawn = {"status": "PARTIAL", "evidence": "05:30 sync/analyzer zinciri halen çalışıyor."}
    elif predawn_service.get("result") != "success" or sync_service.get("result") in {"exit-code", "signal", "timeout", "resources", "core-dump"}:
        predawn = {"status": "FAIL", "evidence": "05:30 zincirinde servis hatası: predawn="
                   + str(predawn_service.get("result")) + ", sync=" + str(sync_service.get("result"))}
    elif not scope_matches_capture:
        predawn = {"status": "PARTIAL", "evidence": "05:30 çalıştı; NightScope henüz bu kayıt gecesiyle eşleşmiyor."}
    elif not matching_sync:
        predawn = {"status": "PARTIAL", "evidence": "05:30 timer görüldü; aynı geceye ait tamamlanmış sync raporu bulunamadı."}
    elif not analysis_for_capture:
        predawn = {"status": "PARTIAL", "evidence": "05:30 sync başarılı; güncel gece için analyzer başlangıcı/ilerlemesi henüz doğrulanmadı."}
    else:
        sync = matching_sync[0]
        predawn = {"status": "PASS",
                   "evidence": ("05:30 timer, sync raporu ve güncel NightScope eşleşti; analyzer başlatıldı. "
                                f"night={capture_night}, sync={sync.get('outcome')}, "
                                f"analyzer={analysis_state}, "
                                f"source-ready={source.get('source_ready_jobs')}, "
                                f"source-missing={source.get('source_missing_jobs')}. "
                                "Bu PASS yalnız başlangıç/scope kapısıdır; kuyruk kapanışı ayrı değerlendirilir.")}

    required_counts = ("WAITING", "RUNNING", "SUCCEEDED", "FAILED", "NO_JOB_SEGMENTS")
    queue_valid = (
        (capture_segments or {}).get("state") == "READ_OK"
        and queue_scope == capture_night
        and all(type(queue.get(k)) is int and queue[k] >= 0 for k in required_counts)
        and all(k in required_counts or (type(v) is int and v == 0)
                for k, v in queue.items())
    )
    def qcount(key):
        return queue[key]

    if predawn["status"] in {"BLOCKED", "FAIL"}:
        analysis_drain = {"status": predawn["status"],
                          "evidence": "05:30 başlangıç kapısı geçmediği için analiz kapanışı kabul edilemez."}
    elif predawn["status"] != "PASS" or not scope_matches_capture:
        analysis_drain = {"status": "PARTIAL",
                          "evidence": "05:30 başlangıcı veya güncel gece kapsamı doğrulanana kadar kuyruk kabulü beklemede."}
    elif not queue_valid:
        analysis_drain = {"status": "BLOCKED",
                          "evidence": "Gece kuyruğu okunamadı, kapsamı uyuşmuyor veya bilinmeyen iş durumu var; boş kuyruk kabul edilmedi."}
    elif analysis_state == "RUNNING" or qcount("RUNNING") > 0:
        analysis_drain = {"status": "PARTIAL",
                          "evidence": f"Analiz sürüyor: WAITING={qcount('WAITING')}, RUNNING={qcount('RUNNING')}, SUCCEEDED={qcount('SUCCEEDED')}, FAILED={qcount('FAILED')}."}
    elif qcount("WAITING") > 0:
        analysis_drain = {"status": "PARTIAL",
                          "evidence": f"Eyleme uygun gece kuyruğunda WAITING={qcount('WAITING')}; 15 dakikalık turlar devam etmeli."}
    elif qcount("FAILED") > 0 or qcount("NO_JOB_SEGMENTS") > 0:
        analysis_drain = {"status": "PARTIAL",
                          "evidence": f"Gece kuyruğunda inceleme gerekiyor: FAILED={qcount('FAILED')}, işi olmayan segment={qcount('NO_JOB_SEGMENTS')}."}
    elif not report_matches_capture or detector.get("analysis_complete") is not True:
        analysis_drain = {"status": "PARTIAL",
                          "evidence": "Kuyruk terminal görünüyor; aynı geceye ait tamamlanmış detector/analiz raporu henüz doğrulanmadı."}
    else:
        analysis_drain = {"status": "PASS",
                          "evidence": f"{capture_night} kapsamındaki işler terminal; FAILED=0, bekleyen=0 ve analiz raporu aynı geceyle eşleşiyor. Tarihsel kuyruk bu kabulün dışındadır."}
    return {"predawn": predawn, "analysis_drain": analysis_drain}

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
                            "-n", "500", "--no-pager", "-o", "json"], 5)
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
              "deletion_receipts_state": "UNAVAILABLE", "last_deleted_utc": None,
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
                "SELECT COUNT(*), COALESCE(SUM(s.size_bytes),0), MAX(d.deleted_utc) "
                "FROM source_dispositions d LEFT JOIN segments s "
                "ON s.device_id=d.device_id AND s.segment_id=d.segment_id "
                "WHERE d.deleted_utc IS NOT NULL").fetchone()
            result["deleted_hdd_files"] = int(row[0])
            result["deleted_hdd_bytes"] = int(row[1])
            result["last_deleted_utc"] = row[2]
            result["deletion_receipts_state"] = "READ_OK"
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
        st = OPS_ALERT_STATE.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or st.st_mode & 0o077 or st.st_size > 8192):
            raise ValueError('unsafe alert state')
        value = json.loads(OPS_ALERT_STATE.read_text(encoding="utf-8"))
        active = value.get("active")
        if (value.get('schema') != 'meteormesh.ops-alert-state.v1' or not isinstance(active, list)
                or len(active) > 150 or not all(isinstance(x, str) and 0 < len(x) < 160 for x in active)):
            raise ValueError('invalid alert state')
        return {"state": "READ_OK", "schema": value.get("schema"),
                "active": active,
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
        elapsed_end = min(observed, end_utc)
        if starts:
            last_start, last_duration = starts[-1]
            last_end = last_start + timedelta(seconds=last_duration or 300)
            trailing = (elapsed_end - last_end).total_seconds()
            if trailing > 360:
                gaps.append({"device_id": device, "from_local": iso_local(last_end),
                             "to_local": iso_local(elapsed_end),
                             "interval_seconds": round(trailing, 1),
                             "missing_seconds": round(trailing, 1),
                             "kind": "TRAILING_UNVERIFIED"})
        elif (elapsed_end - start_utc).total_seconds() > 360:
            missing = (elapsed_end - start_utc).total_seconds()
            gaps.append({"device_id": device, "from_local": iso_local(start_utc),
                         "to_local": iso_local(elapsed_end),
                         "interval_seconds": round(missing, 1),
                         "missing_seconds": round(missing, 1),
                         "kind": "NO_VERIFIED_COVERAGE"})

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
                    "phase": data.get("phase"),
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
    today_sync_reports = []
    utc_day = datetime.now(timezone.utc).strftime("%Y%m%d")
    try:
        sync_files = sorted(folder.glob(f"morning_{utc_day}T*.json"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        sync_files = []
    for path in sync_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("phase") != "sync":
                continue
            stamp = data.get("finished_utc") or data.get("created_utc")
            today_sync_reports.append({
                "name": path.name,
                "night_id": str(data.get("night_id") or "") or None,
                "finished_local": iso_local(stamp),
                "phase": "sync",
                "outcome": data.get("outcome"),
                "jobs_enqueued": data.get("jobs_enqueued"),
                "sync_good": data.get("sync_good"),
            })
        except (OSError, ValueError, TypeError):
            continue
    return {"state": "READ_OK", "samples": len(items),
            "nights_7d": len(unique_days_7), "nights_30d": len(unique_days_30),
            "items": items[:12], "today_sync_reports": today_sync_reports}


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
    cleanup = dict(night.get("hdd_negative_cleanup") or {})
    cleanup.update(read_retention_policy())
    if cleanup.get("auto_delete_enabled") is not True:
        cleanup["state"] = ("DISABLED_AWAITING_SAFETY_VALIDATION"
                            if cleanup.get("policy_state") == "DISABLED" else "POLICY_UNAVAILABLE")
        # The marker does not encode all current user approvals; do not guess.
        cleanup["approval_required_before_enabling"] = None
    system_info = read_system(summary)
    if system_info.get("deletion_receipts_state") == "READ_OK":
        cleanup["last_deleted_utc"] = system_info.get("last_deleted_utc")
    cleanup_gates = retention_acceptance(cleanup, system_info.get("dispositions") or {})
    cleanup.update(cleanup_gates)
    segments = current_segments(now_utc)
    analysis_scope = read_analysis_scope()
    ops_alerts = read_ops_alert_state()
    level, overall_codes = merge_local_alerts(level, assessment.get("codes"), ops_alerts)
    auto_recovery = dict(summary.get("auto_recovery") or {})
    if isinstance(auto_recovery.get("listener_restart"), str):
        auto_recovery["listener_restart"] = "BOUNDED_RESTART_LISTENER_REVIEWED_APP_VERSIONS"
    auto_recovery["hdd_raw_delete"] = cleanup.get("state", "UNKNOWN")
    capture_night_id = segments.get("night_id")
    report_night_id = night.get("night_id")
    scope_matches_capture = (analysis_scope.get("state") == "READ_OK" and
                             analysis_scope.get("night_id") == capture_night_id)
    report_matches_capture = report_night_id == capture_night_id
    capture_queue = segments.get("analysis_queue") or {}
    if segments.get("state") != "READ_OK":
        scope_due = None
        capture_analysis_state = "QUEUE_UNAVAILABLE"
    elif not scope_matches_capture:
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
    timers = read_timers()
    analysis_execution = read_analysis_execution()
    predawn_execution = read_unit_state("meteormesh-predawn.service")
    morning_execution = read_unit_state("meteormesh-morning.service")
    reports = read_recent_reports()
    automation_runs = {
        "predawn": predawn_execution,
        "morning": morning_execution,
        "natural_timer_gates": natural_timer_gates(
            timers, {"predawn": predawn_execution, "morning": morning_execution},
            analysis_execution, reports, capture_night_id, now_utc, segments,
            scope_matches_capture=scope_matches_capture,
            report_matches_capture=report_matches_capture,
            carryover=night.get("carryover_queue") or {}, detector=capture_detector),
    }
    return {
        "schema": "meteormesh.monitor.v1",
        "generated_utc": created,
        "generated_local": iso_local(created),
        "source_error": source_error,
        "source": "RC215 güncel OpsSummary (--preview) + canlı RC191 temizlik politikası",
        "freshness": {"state": "STALE" if stale else "CURRENT",
                      "http_last_success_utc": heartbeat_time,
                      "heartbeat_age_seconds": heartbeat_age,
                      "per_device": per_device_seen,
                      "max_age_seconds": 600,
                      "phone_source_state": hb.get("state", "UNKNOWN")},
        "overall": {"level": level,
                    "codes": overall_codes,
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
        "system": system_info,
        "timers": timers,
        "analysis_execution": analysis_execution,
        "automation_runs": automation_runs,
        "auto_recovery": auto_recovery,
        "ops_alerts": ops_alerts,
        "backup": summary.get("backup") or {},
        "network_readiness": summary.get("network_readiness") or {},
        "reports": reports,
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
             "status": cleanup_gates["software_status"],
             "evidence": cleanup_gates["software_evidence"]},
            {"area": "software", "name": "Bilgi Monitörü localhost salt-okunur",
             "status": "PARTIAL", "evidence": "5 Kasım'da F7 Ultra üzerinden HTTPS monitör sayfası görüntülendi; mobil yenileme ve tüm ekranların kabulü bekliyor"},
            {"area": "software", "name": "Rollback ve sürümlü G62 yayın",
             "status": "PASS", "evidence": "Sürümlü monitor yayınları ve önceki sürüme dönüş yapılandırması mevcut"},
            {"area": "software", "name": "G62 SMART güncel ölçüm",
             "status": "PARTIAL", "evidence": "Monitör sürekli SMART taraması yapmıyor; önceki 446 reallocated sektör kaydı var"},
            {"area": "operations", "name": "Doğal 19:00 başlangıç ve 05:00 tam gece",
             "status": "PARTIAL", "evidence": "Cihazların güncel kayıt durumu telefonlar bölümünde; doğal gece başlangıcı, kapanışı ve kapsamı ayrıca doğrulanmalı"},
            {"area": "operations", "name": "Üç cihazın gerçek segment ilerlemesi",
             "status": "PARTIAL", "evidence": "Cihazların güncel segment durumu gece kayıtları bölümünde; tüm gece sürekliliği ayrıca doğrulanmalı"},
            {"area": "operations", "name": "G62 aktarımı ve SQLite/rapor eşleşmesi",
             "status": "PARTIAL", "evidence": "Aktarım ve rapor bilgileri ilgili bölümlerde; gece kapsamı ve fiş eşleşmesi ayrıca doğrulanmalı"},
            {"area": "operations", "name": "05:30 doğal sync ve güncel NightScope başlangıcı",
             "status": automation_runs["natural_timer_gates"]["predawn"]["status"],
             "evidence": automation_runs["natural_timer_gates"]["predawn"]["evidence"]},
            {"area": "operations", "name": "05:30 analiz kuyruğu ve sonuç kapsamı",
             "status": automation_runs["natural_timer_gates"]["analysis_drain"]["status"],
             "evidence": automation_runs["natural_timer_gates"]["analysis_drain"]["evidence"]},
            {"area": "operations", "name": "Negatif MP4 pilotu ve otomatik silme",
             "status": cleanup_gates["operations_status"],
             "evidence": cleanup_gates["operations_evidence"]},
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
    server_version = "MeteorMeshMonitor/RC188b"
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
