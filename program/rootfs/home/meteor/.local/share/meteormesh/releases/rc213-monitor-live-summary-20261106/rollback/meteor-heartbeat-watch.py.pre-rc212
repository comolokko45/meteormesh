#!/usr/bin/env python3
"""Read-only, channel-separated phone observer for the existing Control-013 timer.

HTTP API, Control-013 heartbeat, ADB transport, recording and handset storage are
reported independently. ADB is diagnostic only and never contributes to the
MeteorNode/recording availability decision. --commit writes only local observer
state under ~/.local/state/meteormesh.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import ssl
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib import error, request

HOME = Path.home()
STATE = HOME / ".local/state/meteormesh/heartbeat-watch-v2.json"
CERT = HOME / ".local/share/meteormesh/control-013/server.crt"
CONTROL_URL = "https://127.0.0.1:8443/v1/status"
PHONES = {
    "viva": {"control_id": "cam-viva", "ip": "192.168.0.216"},
    "ruby": {"control_id": "cam-ruby", "ip": "192.168.0.237"},
    "surya": {"control_id": "cam-surya", "ip": "192.168.0.105"},
}
SCHEMA = "meteormesh.heartbeat-watch.v2"
CONTROL_TTL_SECONDS = 180
OFFLINE_CONFIRM_SECONDS = 120
OFFLINE_CONFIRM_FAILURES = 2
MAX_JSON_BYTES = 16384


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def parse_time(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def seconds_since(value: object, now: dt.datetime) -> int | None:
    parsed = parse_time(value)
    return max(0, int((now - parsed).total_seconds())) if parsed else None


def read_json_response(url: str, *, context: ssl.SSLContext | None = None,
                       timeout: float = 5) -> dict:
    req = request.Request(url, headers={"Accept": "application/json"})
    with request.urlopen(req, context=context, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError("HTTP_" + str(response.status))
        raw = response.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise RuntimeError("RESPONSE_TOO_LARGE")
    try:
        obj = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        raise RuntimeError("INVALID_JSON") from None
    if not isinstance(obj, dict):
        raise RuntimeError("JSON_OBJECT_REQUIRED")
    return obj


def probe_control() -> dict:
    if CERT.is_symlink() or not CERT.is_file():
        return {"ok": False, "error": "CONTROL_CERT_MISSING_OR_SYMLINK", "devices": {}}
    try:
        context = ssl.create_default_context(cafile=str(CERT))
        obj = read_json_response(CONTROL_URL, context=context)
        if obj.get("schema") != "meteormesh.control-status.v1":
            raise RuntimeError("CONTROL_SCHEMA_UNEXPECTED")
        rows = obj.get("devices")
        if not isinstance(rows, dict):
            raise RuntimeError("CONTROL_DEVICE_MAP_UNEXPECTED")
        result = {}
        for name, cfg in PHONES.items():
            row = rows.get(cfg["control_id"])
            if not isinstance(row, dict) or type(row.get("online")) is not bool:
                raise RuntimeError("CONTROL_DEVICE_INVALID_" + name.upper())
            last_seen = row.get("last_seen_utc")
            if last_seen is not None and parse_time(last_seen) is None:
                raise RuntimeError("CONTROL_TIMESTAMP_INVALID_" + name.upper())
            age = row.get("age_seconds")
            if age is not None and (type(age) is not int or age < 0):
                raise RuntimeError("CONTROL_AGE_INVALID_" + name.upper())
            result[name] = {
                "online": row["online"],
                "last_seen_utc": last_seen,
                "age_seconds": age,
                "camera_state": row.get("camera_state"),
                "lan_listening": row.get("lan_listening"),
                "ready_segments": row.get("ready_segments"),
                "free_mib": row.get("free_mib"),
                "app_version": row.get("app_version"),
                "command_pending": row.get("command_pending") is True,
            }
        return {"ok": True, "devices": result}
    except (OSError, RuntimeError, ValueError, ssl.SSLError, error.URLError,
            TimeoutError, json.JSONDecodeError) as exc:
        code = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__.upper()
        return {"ok": False, "error": code[:80], "devices": {}}


def probe_http(ip: str, expected_id: str) -> dict:
    try:
        health = read_json_response(f"http://{ip}:8080/api/v1/health", timeout=4)
        if health.get("device_id") != expected_id:
            raise RuntimeError("DEVICE_ID_MISMATCH")
        version = health.get("app_version")
        state = health.get("state")
        if not isinstance(version, str) or not version.startswith("0."):
            raise RuntimeError("APP_VERSION_INVALID")
        if not isinstance(state, str) or len(state) > 120:
            raise RuntimeError("HEALTH_STATE_INVALID")
        active_id = health.get("recording_active_segment_id")
        if not isinstance(active_id, str):
            active_id = ""
        remote_camera_ready = health.get("remote_camera_ready")
        if type(remote_camera_ready) is not bool:
            remote_camera_ready = None
        free = health.get("free_bytes")
        reserve = health.get("protected_storage_reserve_bytes")
        if type(free) is not int or free < 0:
            free = None
        if type(reserve) is not int or reserve < 0:
            reserve = None
        reserve_reported = health.get("protected_storage_reserve_ok")
        if type(reserve_reported) is not bool:
            reserve_reported = None
        def bounded_int(key, maximum):
            value = health.get(key)
            return value if type(value) is int and 0 <= value <= maximum else None
        last_switch_ms = bounded_int("recording_last_switch_utc_ms", 9_999_999_999_999)
        segment_target_ms = bounded_int("recording_segment_target_ms", 3_600_000)
        last_completed_ms = bounded_int("recording_last_completed_utc_ms", 9_999_999_999_999)
        ready_segments = bounded_int("ready_segments", 10_000_000)
        boundary_verified = health.get("recording_segment_boundary_verified")
        if type(boundary_verified) is not bool:
            boundary_verified = None
        rotation_mode = health.get("recording_rotation_mode")
        if not isinstance(rotation_mode, str) or len(rotation_mode) > 120:
            rotation_mode = None
        if state.startswith("recording:") or (state == "recording" and active_id):
            recording_state = "RECORDING"
        elif state == "error" or bool(health.get("last_error")):
            recording_state = "ERROR"
        elif state in {"idle", "armed_idle", "stopped"}:
            recording_state = "NOT_RECORDING"
        else:
            recording_state = "UNKNOWN"
        storage_state = "UNKNOWN"
        if free is not None and reserve is not None:
            below = free < reserve or reserve_reported is False
            storage_state = "LOW_BELOW_PROTECTED_RESERVE" if below else "OK"
        return {
            "ok": True,
            "data": {
                "device_id": expected_id,
                "app_version": version,
                "node_version": health.get("node_version"),
                "state": state,
                "lan_status": health.get("lan_status"),
                "camera_permission": (health.get("permissions", {}).get("camera")
                                      if isinstance(health.get("permissions"), dict) else None),
                "remote_camera_ready": remote_camera_ready,
                "last_error_present": bool(health.get("last_error")),
                "recording": {
                    "status": recording_state,
                    "active_segment_id": active_id or None,
                    "remote_camera_ready": remote_camera_ready,
                    "recording_last_switch_utc_ms": last_switch_ms,
                    "recording_segment_target_ms": segment_target_ms,
                    "last_completed_mp4_utc_ms": last_completed_ms,
                    "ready_segments": ready_segments,
                    "segment_boundary_verified": boundary_verified,
                    "rotation_mode": rotation_mode,
                },
                "storage": {
                    "status": storage_state,
                    "free_bytes": free,
                    "free_gib": round(free / 1024**3, 2) if free is not None else None,
                    "protected_reserve_bytes": reserve,
                    "reserve_ok": (free >= reserve and reserve_reported is not False)
                                  if free is not None and reserve is not None else None,
                },
            },
        }
    except (OSError, RuntimeError, ValueError, error.URLError, TimeoutError) as exc:
        if isinstance(exc, error.HTTPError):
            reason = "HTTP_" + str(exc.code)
        elif isinstance(exc, RuntimeError):
            reason = str(exc)
        else:
            reason = type(exc).__name__.upper()
        return {"ok": False, "error": reason[:80]}


def probe_adb() -> dict:
    adb = shutil.which("adb")
    if not adb or not Path(adb).is_file():
        return {"ok": False, "error": "ADB_UNAVAILABLE", "devices": {}}
    try:
        result = subprocess.run([adb, "devices", "-l"], capture_output=True,
                                text=True, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": type(exc).__name__.upper(), "devices": {}}
    if result.returncode != 0:
        return {"ok": False, "error": "ADB_EXIT_" + str(result.returncode), "devices": {}}
    by_ip = {cfg["ip"]: {"status": "DISCONNECTED", "model": None}
             for cfg in PHONES.values()}
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        ip = serial.rsplit(":", 1)[0] if ":" in serial else ""
        for cfg in PHONES.values():
            if ip == cfg["ip"]:
                model = next((p[6:] for p in parts[2:] if p.startswith("model:")), None)
                by_ip[ip] = {"status": state.upper() if state != "device" else "CONNECTED",
                             "model": model}
    return {"ok": True, "devices": {
        name: by_ip[cfg["ip"]] for name, cfg in PHONES.items()
    }}


def update_connection(previous: object, status: str, success: bool,
                      now: dt.datetime, error_code: str | None = None) -> dict:
    old = previous if isinstance(previous, dict) else {}
    last_good = old.get("last_success_utc")
    if parse_time(last_good) is None:
        last_good = None
    if success:
        last_good = iso(now)
        outage = None
        failures = 0
        duration = 0
    else:
        outage = old.get("outage_started_utc")
        if parse_time(outage) is None:
            outage = iso(now)
        failures = old.get("consecutive_failures", 0)
        if type(failures) is not int or failures < 0:
            failures = 0
        failures = min(10000, failures + 1)
        duration = seconds_since(outage, now) or 0
    return {
        "status": status,
        "last_success_utc": last_good,
        "outage_started_utc": outage,
        "outage_duration_seconds": duration,
        "consecutive_failures": failures,
        "last_error": None if success else (error_code or status),
    }


def update_control(previous: object, control_ok: bool, row: dict | None,
                   now: dt.datetime, global_error: str | None) -> dict:
    old = previous if isinstance(previous, dict) else {}
    if not control_ok:
        prior = dict(old)
        prior["status"] = "UNKNOWN"
        prior["last_error"] = global_error or "CONTROL_STATUS_UNAVAILABLE"
        prior["observed_at_utc"] = iso(now)
        return prior
    if row is None or not row.get("last_seen_utc"):
        previous_seen = parse_time(old.get("last_success_utc"))
        if previous_seen is None:
            return {"status": "NEVER_SEEN", "last_success_utc": None,
                    "outage_started_utc": None,
                    "outage_duration_seconds": None,
                    "last_error": None, "observed_at_utc": iso(now)}
        last_success = iso(previous_seen)
        age_seconds = seconds_since(last_success, now)
        if age_seconds is None:
            prior = dict(old)
            prior.update({"status": "UNKNOWN",
                          "last_success_utc": last_success,
                          "last_error": "CONTROL_HEARTBEAT_AGE_UNVERIFIED",
                          "observed_at_utc": iso(now)})
            return prior
        outage_start = previous_seen + dt.timedelta(
            seconds=CONTROL_TTL_SECONDS)
        if age_seconds > CONTROL_TTL_SECONDS:
            return {**old, "status": "STALE",
                    "last_success_utc": last_success,
                    "outage_started_utc": iso(outage_start),
                    "outage_duration_seconds": max(
                        0, int((now - outage_start).total_seconds())),
                    "age_seconds": age_seconds,
                    "last_error": "CONTROL_HEARTBEAT_ROW_MISSING",
                    "observed_at_utc": iso(now)}
        return {**old, "status": "UNKNOWN",
                "last_success_utc": last_success,
                "outage_started_utc": None,
                "outage_duration_seconds": None,
                "age_seconds": age_seconds,
                "last_error": "CONTROL_HEARTBEAT_ROW_MISSING",
                "observed_at_utc": iso(now)}
    last_seen = parse_time(row["last_seen_utc"])
    if last_seen is None:
        prior = dict(old)
        prior.update({"status": "UNKNOWN",
                      "last_error": "INVALID_HEARTBEAT_TIMESTAMP",
                      "observed_at_utc": iso(now)})
        return prior
    if row.get("online") is True:
        return {"status": "ONLINE", "last_success_utc": iso(last_seen),
                "outage_started_utc": None, "outage_duration_seconds": 0,
                "age_seconds": seconds_since(iso(last_seen), now),
                "camera_state": row.get("camera_state"),
                "lan_listening": row.get("lan_listening"),
                "ready_segments": row.get("ready_segments"),
                "free_mib": row.get("free_mib"),
                "app_version": row.get("app_version"),
                "command_pending": row.get("command_pending") is True,
                "last_error": None, "observed_at_utc": iso(now)}
    outage_start = last_seen + dt.timedelta(seconds=CONTROL_TTL_SECONDS)
    return {"status": "STALE", "last_success_utc": iso(last_seen),
            "outage_started_utc": iso(outage_start),
            "outage_duration_seconds": max(0, int((now - outage_start).total_seconds())),
            "age_seconds": seconds_since(iso(last_seen), now),
            "camera_state": row.get("camera_state"),
            "lan_listening": row.get("lan_listening"),
            "ready_segments": row.get("ready_segments"),
            "free_mib": row.get("free_mib"),
            "app_version": row.get("app_version"),
            "command_pending": row.get("command_pending") is True,
            "last_error": None, "observed_at_utc": iso(now)}


SAFE_RECOVERY_APP_VERSION = "0.13.5-pin0000-timed-muxer-alpha"
SAFE_RECOVERY_APP_VERSIONS = frozenset({SAFE_RECOVERY_APP_VERSION,
                                        "0.13.6-pin0000-timed-muxer-beta1"})
SAFE_RECOVERY_COOLDOWN_SECONDS = 900
SAFE_RECOVERY_MAX_ATTEMPTS_PER_HOUR = 2
SAFE_RECOVERY_WINDOW_SECONDS = 3600
CONTROL_COMMAND_URL = CONTROL_URL.rsplit("/", 1)[0] + "/command"


def listener_recovery_candidates(control: dict, phones: dict, updated: dict,
                                 now: dt.datetime) -> list[str]:
    """Allow only reviewed app versions with a fresh dead-listener heartbeat."""
    if control.get("ok") is not True:
        return []
    result = []
    rows = control.get("devices", {})
    for name in PHONES:
        row = rows.get(name)
        channel = (updated.get("devices", {}).get(name, {}).get("channels", {})
                   .get("http_api", {}))
        probe = phones.get(name, {})
        age = row.get("age_seconds") if isinstance(row, dict) else None
        if not (isinstance(row, dict) and row.get("online") is True
                and type(age) is int and 0 <= age <= CONTROL_TTL_SECONDS
                and row.get("lan_listening") is False
                and isinstance(row.get("app_version"), str)
                and row.get("app_version") in SAFE_RECOVERY_APP_VERSIONS
                and row.get("command_pending") is False
                and probe.get("ok") is False
                and channel.get("status") == "UNREACHABLE"
                and isinstance(channel.get("consecutive_failures"), int)
                and channel.get("consecutive_failures", 0) >= 2
                and isinstance(channel.get("outage_duration_seconds"), int)
                and channel.get("outage_duration_seconds", 0) >= 60):
            continue
        result.append(name)
    return result


def queue_listener_restart(device_id: str) -> dict:
    """Queue only restart_listener over the existing certificate-pinned TLS endpoint."""
    if CERT.is_symlink() or not CERT.is_file():
        raise RuntimeError("CONTROL_CERT_MISSING_OR_SYMLINK")
    context = ssl.create_default_context(cafile=str(CERT))
    payload = json.dumps({"device_id": device_id, "action": "restart_listener"},
                         separators=(",", ":")).encode("utf-8")
    req = request.Request(CONTROL_COMMAND_URL, data=payload, method="POST",
                         headers={"Accept": "application/json",
                                   "Content-Type": "application/json"})
    try:
        with request.urlopen(req, context=context, timeout=5) as response:
            code = response.status
            raw = response.read(MAX_JSON_BYTES + 1)
    except error.HTTPError as exc:
        raise RuntimeError("CONTROL_COMMAND_HTTP_" + str(exc.code)) from None
    if code != 202 or len(raw) > MAX_JSON_BYTES:
        raise RuntimeError("CONTROL_COMMAND_RESPONSE_INVALID")
    try:
        obj = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        raise RuntimeError("CONTROL_COMMAND_INVALID_JSON") from None
    command_id = obj.get("command_id") if isinstance(obj, dict) else None
    if (obj.get("queued") is not True or obj.get("device_id") != device_id
            or not isinstance(command_id, str) or len(command_id) != 32
            or any(ch not in "0123456789abcdef" for ch in command_id)):
        raise RuntimeError("CONTROL_COMMAND_ACK_INVALID")
    return {"status": "QUEUED", "device_id": device_id}


def apply_safe_listener_recovery(control: dict, phones: dict, updated: dict,
                                 now: dt.datetime, *, commit: bool,
                                 sender=queue_listener_restart,
                                 save_state=None) -> dict:
    targets = listener_recovery_candidates(control, phones, updated, now)
    actions = []
    for name in targets:
        device = PHONES[name]["control_id"]
        row = updated["devices"][name]
        recovery = row.get("safe_recovery")
        if not isinstance(recovery, dict):
            recovery = {}
        attempts = recovery.get("attempts_utc", [])
        if not isinstance(attempts, list):
            attempts = []
        recent = []
        for stamp in attempts:
            parsed = parse_time(stamp)
            if parsed is not None and (now - parsed).total_seconds() <= SAFE_RECOVERY_WINDOW_SECONDS:
                recent.append(iso(parsed))
        last = parse_time(recent[-1]) if recent else None
        if len(recent) >= SAFE_RECOVERY_MAX_ATTEMPTS_PER_HOUR:
            actions.append({"device": name, "status": "BACKOFF_MAX_PER_HOUR"})
            continue
        if last is not None and (now - last).total_seconds() < SAFE_RECOVERY_COOLDOWN_SECONDS:
            actions.append({"device": name, "status": "BACKOFF_COOLDOWN"})
            continue
        if not commit:
            actions.append({"device": name, "status": "DRY_RUN_ELIGIBLE"})
            continue
        recent.append(iso(now))
        recovery.update({"attempts_utc": recent[-SAFE_RECOVERY_MAX_ATTEMPTS_PER_HOUR:],
                         "last_attempt_utc": iso(now), "last_result": "REQUESTING"})
        row["safe_recovery"] = recovery
        if save_state is not None:
            save_state(updated)
        try:
            response = sender(device)
            recovery.update({"last_result": response["status"],
                             "last_command_queued_utc": iso(now)})
            actions.append({"device": name, "status": response["status"]})
        except (OSError, RuntimeError, ValueError, ssl.SSLError,
                error.URLError, TimeoutError) as exc:
            recovery["last_result"] = "FAILED_" + type(exc).__name__
            actions.append({"device": name, "status": recovery["last_result"]})
        row["safe_recovery"] = recovery
        if save_state is not None:
            save_state(updated)
    return {"mode": "COMMIT" if commit else "DRY_RUN",
            "allowed_action": "restart_listener_only", "actions": actions}


def verify_listener_observations(updated: dict, now: dt.datetime) -> list[dict]:
    """Record a later HTTP observation, without claiming what caused recovery."""
    observations = []
    devices = updated.get('devices', {})
    for name in PHONES:
        row = devices.get(name, {}) if isinstance(devices, dict) else {}
        recovery = row.get('safe_recovery') if isinstance(row, dict) else None
        if not isinstance(recovery, dict) or recovery.get('last_result') != 'QUEUED':
            continue
        queued = parse_time(recovery.get('last_command_queued_utc'))
        if queued is None:
            continue
        age = (now - queued).total_seconds()
        if age < 0:
            continue
        channels = row.get('channels', {})
        http = channels.get('http_api', {}) if isinstance(channels, dict) else {}
        if age <= SAFE_RECOVERY_WINDOW_SECONDS and isinstance(http, dict) and http.get('status') == 'ONLINE':
            recovery['last_result'] = 'HTTP_REACHABLE_AFTER_REQUEST'
        elif age > SAFE_RECOVERY_WINDOW_SECONDS:
            recovery['last_result'] = 'UNCONFIRMED_AFTER_TIMEOUT'
        else:
            continue
        recovery['last_verification_utc'] = iso(now)
        observations.append({'device': name, 'status': recovery['last_result']})
    return observations


def evaluate(control: dict, phones: dict, adb: dict, previous: dict,
             now: dt.datetime | None = None) -> tuple[dict, list[str], dict]:
    now = now or utc_now()
    prior_devices = previous.get("devices", {}) if isinstance(previous, dict) else {}
    updates: dict = {}
    events: list[str] = []
    output: dict = {}
    for name, cfg in PHONES.items():
        old = prior_devices.get(name, {}) if isinstance(prior_devices, dict) else {}
        if not isinstance(old, dict):
            old = {}
        old_channels = old.get("channels", {})
        if not isinstance(old_channels, dict):
            old_channels = {}

        http_result = phones.get(name, {"ok": False, "error": "NO_HTTP_RESULT"})
        http_ok = http_result.get("ok") is True
        http_channel = update_connection(
            old_channels.get("http_api"), "ONLINE" if http_ok else "UNREACHABLE",
            http_ok, now, http_result.get("error"))

        adb_result = adb.get("devices", {}).get(name, {"status": "UNKNOWN"})
        adb_status = adb_result.get("status", "UNKNOWN")
        adb_ok = adb.get("ok") is True and adb_status == "CONNECTED"
        adb_known = adb.get("ok") is True
        if adb_known:
            adb_channel = update_connection(old_channels.get("adb"), adb_status,
                                            adb_ok, now,
                                            None if adb_ok else "ADB_" + adb_status)
            adb_channel["model"] = adb_result.get("model")
        else:
            adb_channel = dict(old_channels.get("adb", {}))
            adb_channel.update({"status": "UNKNOWN", "last_error": adb.get("error"),
                                "observed_at_utc": iso(now)})

        ctrl_row = control.get("devices", {}).get(name)
        control_channel = update_control(
            old_channels.get("control_013"), control.get("ok") is True,
            ctrl_row, now, control.get("error"))

        data = http_result.get("data", {}) if http_ok else {}
        recording = dict(data.get("recording", {})) if http_ok else {
            "status": "UNKNOWN_HTTP_UNAVAILABLE", "active_segment_id": None}
        storage = dict(data.get("storage", {})) if http_ok else {
            "status": "UNKNOWN_HTTP_UNAVAILABLE", "free_bytes": None,
            "free_gib": None, "protected_reserve_bytes": None, "reserve_ok": None}
        previous_recording = old_channels.get("recording", {})
        if not isinstance(previous_recording, dict):
            previous_recording = {}
        was_recording = previous_recording.get("status") == "RECORDING"
        is_recording = recording.get("status") == "RECORDING"
        recording["last_sample_utc"] = iso(now) if http_ok else previous_recording.get("last_sample_utc")
        recording["last_recording_utc"] = (iso(now) if is_recording else
            previous_recording.get("last_recording_utc"))
        recording["gap_started_utc"] = (None if is_recording else
            previous_recording.get("gap_started_utc") or (iso(now) if was_recording else None))
        recording["gap_duration_seconds"] = seconds_since(recording.get("gap_started_utc"), now)
        previous_storage = old_channels.get("storage", {})
        if not isinstance(previous_storage, dict):
            previous_storage = {}
        storage["last_sample_utc"] = (iso(now) if http_ok and storage.get("status") != "UNKNOWN"
                                      else previous_storage.get("last_sample_utc"))
        # Expose the last time each independent connection was observed healthy.
        http_channel["ip"] = cfg["ip"]
        http_channel["port"] = 8080
        control_channel["heartbeat_ttl_seconds"] = CONTROL_TTL_SECONDS
        adb_channel["ip"] = cfg["ip"]

        if http_ok:
            if control_channel["status"] == "ONLINE":
                node_state = "OPERATIONAL"
            elif control_channel["status"] == "STALE":
                node_state = "OPERATIONAL_CONTROL_HEARTBEAT_STALE"
            else:
                node_state = "OPERATIONAL_CONTROL_HEARTBEAT_UNKNOWN"
        elif control_channel["status"] == "ONLINE":
            node_state = "HTTP_API_UNAVAILABLE_CONTROL_HEARTBEAT_ONLINE"
        else:
            outage_seconds = http_channel.get("outage_duration_seconds", 0) or 0
            confirmed = (control_channel["status"] == "STALE"
                         and control_channel.get("last_success_utc") is not None
                         and http_channel["consecutive_failures"] >= OFFLINE_CONFIRM_FAILURES
                         and outage_seconds >= OFFLINE_CONFIRM_SECONDS)
            if confirmed:
                node_state = "OFFLINE_CONFIRMED"
            elif control_channel["status"] == "STALE":
                node_state = "OFFLINE_PENDING_CONFIRMATION"
            else:
                node_state = "SERVICE_STATUS_DEGRADED"

        old_node_offline = old.get("node_offline_confirmed") is True
        now_offline = node_state == "OFFLINE_CONFIRMED"
        if now_offline and not old_node_offline:
            events.append("NODE_OFFLINE_CONFIRMED:" + cfg["control_id"])
        elif old_node_offline and not now_offline:
            events.append("NODE_OFFLINE_CONDITION_CLEARED:" + cfg["control_id"])
        old_control_state = old_channels.get("control_013", {}).get("status") if isinstance(old_channels.get("control_013"), dict) else None
        if control_channel["status"] == "STALE" and old_control_state == "ONLINE":
            events.append("CONTROL_HEARTBEAT_STALE:" + cfg["control_id"])
        elif control_channel["status"] == "ONLINE" and old_control_state == "STALE":
            events.append("CONTROL_HEARTBEAT_RECOVERED:" + cfg["control_id"])

        channels = {"http_api": http_channel, "adb": adb_channel,
                    "control_013": control_channel, "recording": recording,
                    "storage": storage}
        updates[name] = {"channels": channels, "node_offline_confirmed": now_offline,
                          "safe_recovery": old.get("safe_recovery", {})}
        output[name] = {"meteor_node_state": node_state, **channels,
                        "app_version": data.get("app_version") if http_ok else None,
                        "node_version": data.get("node_version") if http_ok else None,
                        "lan_status": data.get("lan_status") if http_ok else None,
                        "camera_permission": data.get("camera_permission") if http_ok else None,
                        "remote_camera_ready": data.get("remote_camera_ready") if http_ok else None,
                        "last_error_present": data.get("last_error_present") if http_ok else None}

    states = [output[n]["meteor_node_state"] for n in PHONES]
    if all(state == "OPERATIONAL" for state in states):
        summary = "ALL_OPERATIONAL"
    elif all(state.startswith("OPERATIONAL") for state in states):
        summary = "NODES_OPERATIONAL_CONTROL_DEGRADED"
    else:
        summary = "DEGRADED"
    report = {
        "schema": SCHEMA,
        "timestamp_utc": iso(now),
        "source": "CONTROL_013_PLUS_HTTP_HEALTH_PLUS_ADB_DEVICES",
        "status": summary,
        "devices": output,
        "events": events,
        "adb_affects_meteor_node_state": False,
        "offline_confirmation": {"http_failures": OFFLINE_CONFIRM_FAILURES,
                                  "minimum_seconds": OFFLINE_CONFIRM_SECONDS,
                                  "requires_stale_control_heartbeat": True},
        "mode": "READ_ONLY_DRY_RUN",
        "production_db_opened": False, "hdd_written": False,
        "phone_commands_sent": False, "recordings_modified": False,
        "external_notification_sent": False,
    }
    return {"schema": SCHEMA, "devices": updates}, events, report


def read_state() -> dict:
    try:
        st = STATE.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_size > 32768
                or st.st_mode & 0o077 or st.st_uid != os.getuid()):
            raise RuntimeError("UNSAFE_STATE_FILE")
        obj = json.loads(STATE.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise RuntimeError("INVALID_STATE")
        if obj.get("schema") == SCHEMA and isinstance(obj.get("devices"), dict):
            return obj
        if obj.get("schema") == "meteormesh.heartbeat-watch.v1":
            return {"schema": SCHEMA, "devices": {}, "migrated_from_v1": True}
        raise RuntimeError("UNEXPECTED_STATE_SCHEMA")
    except FileNotFoundError:
        return {"schema": SCHEMA, "devices": {}}


def persist(state: dict) -> None:
    if os.geteuid() == 0:
        raise RuntimeError("ROOT_NOT_ALLOWED")
    if STATE.is_symlink() or STATE.parent.is_symlink():
        raise RuntimeError("STATE_SYMLINK_BLOCKED")
    parent = STATE.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o077:
        raise RuntimeError("STATE_DIRECTORY_INSECURE")
    if STATE.exists():
        st = STATE.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise RuntimeError("STATE_FILE_INSECURE")
    blob = (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd, tmp = tempfile.mkstemp(prefix=".heartbeat-watch-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, STATE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def selftest() -> None:
    now = dt.datetime(2026, 11, 3, 17, 0, tzinfo=dt.timezone.utc)
    def control(online: bool, seen: str) -> dict:
        return {"ok": True, "devices": {n: {"online": online,
                "last_seen_utc": seen, "age_seconds": 30 if online else 400,
                "camera_state": "recording", "command_pending": False}
                for n in PHONES}}
    http_ok = {n: {"ok": True, "data": {"app_version": "0.13.4",
        "recording": {"status": "RECORDING", "active_segment_id": n + "-s1"},
        "storage": {"status": "OK", "free_bytes": 40_000_000_000,
                    "free_gib": 37.25, "protected_reserve_bytes": 32_000_000_000,
                    "reserve_ok": True}}} for n in PHONES}
    adb_off = {"ok": True, "devices": {n: {"status": "DISCONNECTED"} for n in PHONES}}
    state, events, report = evaluate(control(False, "2026-11-03T16:00:00+00:00"),
                                     http_ok, adb_off, {}, now)
    assert all(report["devices"][n]["meteor_node_state"] ==
               "OPERATIONAL_CONTROL_HEARTBEAT_STALE" for n in PHONES)
    assert all(report["devices"][n]["recording"]["status"] == "RECORDING" for n in PHONES)
    assert all(report["devices"][n]["adb"]["status"] == "DISCONNECTED" for n in PHONES)
    assert all(report["devices"][n]["http_api"]["last_success_utc"] == iso(now) for n in PHONES)
    assert all(report["devices"][n]["adb"]["outage_started_utc"] == iso(now) for n in PHONES)
    assert all(report["devices"][n]["storage"]["reserve_ok"] is True for n in PHONES)
    assert not any("OFFLINE" in e for e in events)

    previous_control = {
        "status": "STALE",
        "last_success_utc": "2026-11-03T16:00:00+00:00",
        "outage_started_utc": "2026-11-03T16:03:00+00:00",
        "outage_duration_seconds": 3420,
    }
    retained = update_control(previous_control, True, None, now, None)
    assert retained["status"] == "STALE"
    assert retained["last_success_utc"] == "2026-11-03T16:00:00+00:00"
    assert retained["outage_started_utc"] == "2026-11-03T16:03:00+00:00"
    assert retained["outage_duration_seconds"] == 3420
    assert retained["age_seconds"] == 3600
    assert retained["last_error"] == "CONTROL_HEARTBEAT_ROW_MISSING"
    invalid_retained = update_control(previous_control, True,
        {"last_seen_utc": "invalid"}, now, None)
    assert invalid_retained["status"] == "UNKNOWN"
    assert invalid_retained["last_success_utc"] == previous_control["last_success_utc"]
    assert update_control({}, True, None, now, None)["status"] == "NEVER_SEEN"

    http_down = {n: {"ok": False, "error": "TimeoutError"} for n in PHONES}
    state1, _, first = evaluate(control(True, "2026-11-03T16:59:50+00:00"),
                                http_down, adb_off, {}, now)
    assert all(first["devices"][n]["meteor_node_state"] != "OFFLINE_CONFIRMED" for n in PHONES)
    state2, offline_events, second = evaluate(control(False, "2026-11-03T16:00:00+00:00"),
        http_down, adb_off, state1, now + dt.timedelta(seconds=121))
    assert all(second["devices"][n]["meteor_node_state"] == "OFFLINE_CONFIRMED" for n in PHONES)
    assert len([e for e in offline_events if e.startswith("NODE_OFFLINE_CONFIRMED")]) == 3
    assert all(second["devices"][n]["http_api"]["outage_duration_seconds"] == 121 for n in PHONES)
    _, recover_events, recovered = evaluate(control(True, "2026-11-03T17:03:00+00:00"),
        http_ok, adb_off, state2, now + dt.timedelta(minutes=3))
    assert all(recovered["devices"][n]["meteor_node_state"].startswith("OPERATIONAL") for n in PHONES)
    assert len([e for e in recover_events if e.startswith("NODE_OFFLINE_CONDITION_CLEARED")]) == 3
    assert all(recovered["devices"][n]["http_api"]["outage_duration_seconds"] == 0 for n in PHONES)
    now = dt.datetime(2026, 11, 4, 12, 0, tzinfo=dt.timezone.utc)
    ctrl = {"ok": True, "devices": {"viva": {"online": True, "age_seconds": 60,
        "lan_listening": False, "app_version": SAFE_RECOVERY_APP_VERSION,
        "command_pending": False}}}
    http = {"viva": {"ok": False}}
    state = {"devices": {"viva": {"channels": {"http_api": {
        "status": "UNREACHABLE", "consecutive_failures": 3,
        "outage_duration_seconds": 300}}, "safe_recovery": {}}}}
    assert listener_recovery_candidates(ctrl, http, state, now) == ["viva"]
    calls = []
    def fake_sender(device):
        calls.append(device)
        return {"status": "QUEUED", "device_id": device}
    preview = apply_safe_listener_recovery(ctrl, http, state, now, commit=False,
                                           sender=fake_sender)
    assert calls == [] and preview["actions"] == [{"device": "viva", "status": "DRY_RUN_ELIGIBLE"}]
    committed = apply_safe_listener_recovery(ctrl, http, state, now, commit=True,
                                             sender=fake_sender)
    assert calls == ["cam-viva"] and committed["actions"][0]["status"] == "QUEUED"
    assert len(state["devices"]["viva"]["safe_recovery"]["attempts_utc"]) == 1
    cooldown = apply_safe_listener_recovery(ctrl, http, state,
        now + dt.timedelta(minutes=5), commit=True, sender=fake_sender)
    assert len(calls) == 1 and cooldown["actions"][0]["status"] == "BACKOFF_COOLDOWN"
    for bad in (("online", False), ("lan_listening", True),
                ("app_version", "0.13.4-pin0000-alpha"), ("command_pending", True)):
        original = ctrl["devices"]["viva"].get(bad[0])
        ctrl["devices"]["viva"][bad[0]] = bad[1]
        assert listener_recovery_candidates(ctrl, http, state, now) == []
        if original is None: ctrl["devices"]["viva"].pop(bad[0], None)
        else: ctrl["devices"]["viva"][bad[0]] = original
    stale = {"ok": True, "devices": {"viva": {**ctrl["devices"]["viva"], "age_seconds": 181}}}
    assert listener_recovery_candidates(stale, http, state, now) == []
    print("SAFE_LISTENER_RECOVERY_SELFTEST: PASS; fresh 0.13.5 Control only; HTTP outage; one allow-listed command; dry-run; cooldown; stale/old/pending rejection")
    print("HEARTBEAT_WATCH_V2_SELFTEST: PASS (ADB-independent node state; hysteresis; recovery; channel timestamps)")


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--commit", action="store_true", help="write observer state; recovery still requires its separate explicit flag")
    mode.add_argument("--self-test", action="store_true")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress routine JSON output; errors still go to stderr")
    parser.add_argument("--allow-safe-listener-recovery", action="store_true",
                        help="permit bounded restart_listener only for reviewed app versions with fresh heartbeat and confirmed HTTP listener outage")
    args = parser.parse_args()
    if args.self_test:
        selftest()
        return 0
    now = utc_now()
    control = probe_control()
    phones = {name: probe_http(cfg["ip"], name) for name, cfg in PHONES.items()}
    adb = probe_adb()
    previous = read_state()
    updated, _, report = evaluate(control, phones, adb, previous, now)
    observations = verify_listener_observations(updated, now)
    report['listener_verification'] = observations
    if args.commit:
        for item in observations:
            print('METEORMESH_AUTOPILOT_LISTENER_OBSERVATION: ' +
                  item['device'] + ':' + item['status'], flush=True)
    if args.allow_safe_listener_recovery:
        recovery = apply_safe_listener_recovery(
            control, phones, updated, now, commit=args.commit,
            save_state=persist if args.commit else None)
        report["safe_recovery"] = recovery
        report["phone_commands_sent"] = any(
            item.get("status") == "QUEUED" for item in recovery["actions"])
        for item in recovery["actions"]:
            if item.get("status", "").startswith("FAILED_") or item.get("status") == "QUEUED":
                print("METEORMESH_AUTOPILOT_LISTENER_ACTION: " +
                      str(item.get("device")) + ":" + str(item.get("status")),
                      flush=True)
    report["control_status_endpoint"] = "ONLINE" if control.get("ok") else "UNAVAILABLE"
    report["control_status_error"] = control.get("error")
    report["adb_command"] = "AVAILABLE" if adb.get("ok") else "UNAVAILABLE"
    report["adb_error"] = adb.get("error")
    report["mode"] = ("COMMIT_WITH_BOUNDED_SAFE_RECOVERY" if args.commit and args.allow_safe_listener_recovery
                      else "COMMIT_LOCAL_OBSERVATION" if args.commit
                      else "READ_ONLY_DRY_RUN")
    if not args.quiet:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    if args.commit:
        persist(updated)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, ssl.SSLError, TimeoutError) as exc:
        print("HEARTBEAT_WATCH_ERROR: " + type(exc).__name__ + ": " + str(exc)[:100],
              file=sys.stderr)
        sys.exit(2)
