"""Fail-closed, per-night ingest and queue restriction (lab/staging v0.1).

UTC inferred from filename is a routing hint, not a calibrated frame UTC.
A DISARMED or invalid policy never authorizes a night job.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, time as clock_time
from zoneinfo import ZoneInfo
from pathlib import Path
import json
import re

SHA = re.compile(r"^[a-f0-9]{64}$")
SEGMENT = re.compile(r"^[A-Za-z0-9_-]+_(\d{8}T\d{6}Z)_[0-9a-f]{12}$")
NIGHT_ID = re.compile(r"^[A-Za-z0-9_-]{5,60}$")
DEVICES = {"cam-viva", "cam-ruby", "cam-surya"}


@dataclass(frozen=True)
class NightScope:
    night_id: str
    start_ms: int
    end_ms: int
    excluded: dict[str, dict[str, str]]
    baseline_ms: int

    def allows(self, device: str, segment_id: str, digest: str) -> bool:
        if device not in DEVICES or not isinstance(segment_id, str):
            return False
        if not isinstance(digest, str) or not SHA.fullmatch(digest):
            return False
        match = SEGMENT.fullmatch(segment_id)
        if match is None:
            return False
        known = self.excluded[device].get(segment_id)
        if known is not None:
            if known != digest:
                raise RuntimeError("Excluded segment digest changed: " + device + "/" + segment_id)
            return False
        try:
            stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        millis = int(stamp.timestamp() * 1000)
        return self.start_ms <= millis < self.end_ms

    def allows_queue(self, device: str, segment_id: str, digest: str) -> bool:
        """Current night OR older verified night after inventory baseline.

        Explicitly excludes daylight and all baseline laboratory recordings.
        This permits queued jobs to survive a new daily --night-scope policy,
        without allowing an arbitrary old job to bypass the baseline.
        """
        if self.allows(device, segment_id, digest):
            return True
        match = SEGMENT.fullmatch(segment_id) if isinstance(segment_id, str) else None
        if match is None:
            return False
        try:
            dt = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        millis = int(dt.timestamp() * 1000)
        if not self.baseline_ms < millis < self.start_ms:
            return False
        local = dt.astimezone(ZoneInfo("Europe/Istanbul")).time()
        return local >= clock_time(19) or local < clock_time(7)


def load_night_scope(path: Path) -> NightScope:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("NIGHT_SCOPE_DENIED: missing or symlink policy")
    if path.stat().st_mode & 0o077:
        raise RuntimeError("NIGHT_SCOPE_DENIED: policy must not be accessible to group/others")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != "meteormesh.night_scope.v1" or doc.get("status") != "ARMED":
        raise RuntimeError("NIGHT_SCOPE_DENIED: policy is not ARMED")
    if doc.get("automatic_run_allowed") is not True:
        raise RuntimeError("NIGHT_SCOPE_DENIED: run not authorized")
    if doc.get("ingest_scope_enforced") is not True or doc.get("queue_scope_enforced") is not True:
        raise RuntimeError("NIGHT_SCOPE_DENIED: scope enforcement not authorized")
    night_id = doc.get("night_id")
    a, b = doc.get("start_utc_ms"), doc.get("end_utc_ms")
    if not isinstance(night_id, str) or not NIGHT_ID.fullmatch(night_id):
        raise RuntimeError("NIGHT_SCOPE_DENIED: invalid night ID")
    if type(a) is not int or type(b) is not int or not (1700000000000 < a < b <= a + 16 * 3600 * 1000):
        raise RuntimeError("NIGHT_SCOPE_DENIED: invalid UTC night window")
    baseline_time = doc.get("baseline_created_utc")
    if not isinstance(baseline_time, str):
        raise RuntimeError("NIGHT_SCOPE_DENIED: missing baseline time")
    try:
        baseline = datetime.fromisoformat(baseline_time.replace("Z", "+00:00"))
        if baseline.tzinfo is None:
            raise ValueError("timezone missing")
        baseline_ms = int(baseline.timestamp() * 1000)
    except ValueError as exc:
        raise RuntimeError("NIGHT_SCOPE_DENIED: invalid baseline time") from exc
    if a <= baseline_ms:
        raise RuntimeError("NIGHT_SCOPE_DENIED: night begins before saved inventory")
    excluded = doc.get("excluded_segments")
    if not isinstance(excluded, dict) or set(excluded) != DEVICES:
        raise RuntimeError("NIGHT_SCOPE_DENIED: missing camera baseline")
    for dev, ids in excluded.items():
        if not isinstance(ids, dict) or not ids:
            raise RuntimeError("NIGHT_SCOPE_DENIED: empty camera baseline")
        for key, value in ids.items():
            if not isinstance(key, str) or SEGMENT.fullmatch(key) is None or not isinstance(value, str) or SHA.fullmatch(value) is None:
                raise RuntimeError("NIGHT_SCOPE_DENIED: malformed excluded segment")
    return NightScope(night_id, a, b, excluded, baseline_ms)
