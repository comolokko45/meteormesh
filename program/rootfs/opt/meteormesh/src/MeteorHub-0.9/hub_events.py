"""Translate MetDetPy MDRF to provisional, never-destructive camera time groups.

Phone v0.1 does not establish precise frame UTC. A temporal grouping from these
timestamps is only a REVIEW PROPOSAL, not evidence of one physical meteor.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from meteornode_pull import VALID_ID


RELATIVE_TIME = re.compile(r"^(\d{1,2}):(\d\d):(\d\d)(?:\.(\d+))?$")


@dataclass(frozen=True)
class Candidate:
    device_id: str
    segment_id: str
    source_sha256: str
    frame: int | None
    utc_ms_estimate: int | None
    relative_ms: int | None
    score: float | None
    category: str
    time_quality: str
    detector_result_sha256: str

    def identifier(self) -> str:
        stable = f"{self.device_id}:{self.segment_id}:{self.frame}:{self.relative_ms}:{self.source_sha256}"
        return hashlib.sha256(stable.encode()).hexdigest()[:20]


def parse_relative_time(value: str) -> int:
    found = RELATIVE_TIME.fullmatch(value)
    if not found:
        raise ValueError(f"Unsupported time format: {value!r}")
    hours, minutes, seconds = [int(found.group(i)) for i in (1, 2, 3)]
    if minutes >= 60 or seconds >= 60:
        raise ValueError("Invalid MM:SS time")
    millis = int((found.group(4) or "0")[:3].ljust(3, "0"))
    return ((hours * 3600 + minutes * 60 + seconds) * 1000) + millis


def candidates_from_mdrf(device: str, sidecar: dict, mdrf: dict,
                         result_sha256: str) -> list[Candidate]:
    if not VALID_ID.fullmatch(device):
        raise ValueError("Unsafe camera id")
    segment = sidecar["segment_id"]
    source_hash = str(sidecar["sha256"]).lower()
    if not VALID_ID.fullmatch(segment) or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise ValueError("Invalid source metadata")
    records = mdrf.get("results")
    if not isinstance(records, list):
        raise ValueError("MDRF missing results array")
    try:
        fps = float(mdrf.get("basic_info", {}).get("fps") or sidecar.get("nominal_fps") or 0)
    except (ValueError, TypeError):
        fps = 0
    origin = sidecar.get("start_utc_ms_estimate")
    if isinstance(origin, bool) or not isinstance(origin, int) or origin < 0:
        origin = None

    candidates = []
    for record in records:
        if not isinstance(record, dict):
            continue
        targets = record.get("target")
        if not isinstance(targets, list):
            continue
        for target in targets:
            if not isinstance(target, dict):
                continue
            frame = target.get("start_frame")
            if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
                frame = None
            relative = None
            source = "UNKNOWN"
            if frame is not None and fps > 0 and math.isfinite(fps):
                relative = round(frame * 1000 / fps)
                source = "FRAME_INDEX_OVER_NOMINAL_FPS"
            elif isinstance(target.get("start_time"), str):
                try:
                    relative = parse_relative_time(target["start_time"])
                    source = "MDRF_RELATIVE_TIME_UNVERIFIED"
                except ValueError:
                    pass
            raw_score = target.get("score")
            if isinstance(raw_score, bool):
                raw_score = None
            if isinstance(raw_score, (float, int)) and math.isfinite(raw_score):
                score = max(0.0, min(1.0, float(raw_score)))
            else:
                score = None
            candidates.append(Candidate(
                device_id=device, segment_id=segment, source_sha256=source_hash,
                frame=frame, relative_ms=relative,
                utc_ms_estimate=(origin + relative if origin is not None and relative is not None else None),
                score=score, category=str(target.get("category", "unknown"))[:100],
                time_quality=f"UNVERIFIED_{source}",
                detector_result_sha256=result_sha256,
            ))
    return candidates


def group_provisional(candidates: list[Candidate], window_ms: int = 2000) -> list[dict]:
    """One candidate per device per time bucket; NEVER a confirmed meteor match."""
    if not (100 <= window_ms <= 60_000):
        raise ValueError("Invalid temporal window")
    groups: list[list[Candidate]] = []
    valid = sorted((c for c in candidates if c.utc_ms_estimate is not None),
                   key=lambda c: (c.utc_ms_estimate, c.device_id, c.identifier()))
    unknown = sorted((c for c in candidates if c.utc_ms_estimate is None),
                     key=lambda c: c.identifier())
    for candidate in valid:
        choice = None
        for group in groups:
            if any(existing.device_id == candidate.device_id for existing in group):
                continue
            # Compare to the group's first candidate; prevent chaining distant events.
            if abs(candidate.utc_ms_estimate - group[0].utc_ms_estimate) <= window_ms:
                choice = group
                break
        if choice is None:
            choice = []
            groups.append(choice)
        choice.append(candidate)
    groups.extend([[c] for c in unknown])
    output = []
    for group in groups:
        identifiers = sorted(c.identifier() for c in group)
        event_id = hashlib.sha256("|".join(identifiers).encode()).hexdigest()[:20]
        output.append({
            "provisional_event_id": f"p_{event_id}",
            "classification": "PROVISIONAL_TIME_NEIGHBORHOOD_ONLY",
            "confirmed_same_meteor": False,
            "utc_ms_estimate": min((c.utc_ms_estimate for c in group
                                    if c.utc_ms_estimate is not None), default=None),
            "cameras": sorted({c.device_id for c in group}),
            "candidates": [asdict(c) | {"candidate_id": c.identifier()} for c in group],
        })
    return output


def load_candidates(db: sqlite3.Connection, data_root: Path) -> list[Candidate]:
    all_candidates = []
    # Re-analysis with a newer configuration must not silently duplicate an
    # event. Use the latest SUCCEEDED run of each source in a daily report.
    rows = db.execute("""SELECT r.device_id, r.segment_id, r.input_sha256,
                        r.result_path, r.result_sha256, s.size_bytes FROM detector_runs r
                        JOIN segments s ON s.device_id=r.device_id AND
                                           s.segment_id=r.segment_id AND
                                           s.sha256=r.input_sha256 AND
                                           s.state='INGEST_VERIFIED'
                        JOIN jobs j ON j.id=r.job_id AND j.state='SUCCEEDED'
                        JOIN (SELECT device_id, segment_id, MAX(job_id) AS latest
                              FROM detector_runs GROUP BY device_id, segment_id) selected
                        ON selected.latest=r.job_id ORDER BY r.device_id, r.segment_id""").fetchall()
    from hub_workflow import sha256_file

    for device, segment, input_sha, result_path, result_sha, expected_size in rows:
        if not VALID_ID.fullmatch(device) or not VALID_ID.fullmatch(segment):
            raise RuntimeError("Unsafe DB source identity")
        sidecar = data_root / device / f"{segment}.json"
        result_file = Path(result_path)
        if sidecar.is_symlink() or result_file.is_symlink():
            raise RuntimeError("Symlink event source is forbidden")
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        if metadata.get("segment_id") != segment or metadata.get("sha256") != input_sha:
            raise RuntimeError(f"Video metadata changed: {device}/{segment}")
        if sha256_file(result_file) != result_sha:
            raise RuntimeError(f"Detector result changed: {result_file}")
        mdrf = json.loads(result_file.read_text(encoding="utf-8"))
        source_candidates = candidates_from_mdrf(device, metadata, mdrf, result_sha)
        if source_candidates:
            video = data_root / device / f"{segment}.mp4"
            if video.is_symlink() or not video.is_file() or video.stat().st_size != expected_size:
                raise RuntimeError(f"Original video changed or missing: {device}/{segment}")
            if sha256_file(video) != input_sha:
                raise RuntimeError(f"Original video changed since ingest: {device}/{segment}")
        all_candidates.extend(source_candidates)
    return all_candidates


def context_for_event(event: dict, data_root: Path, device_ids: list[str],
                      half_window_ms: int = 30_000,
                      ingested: set[tuple[str, str, str]] | None = None) -> dict:
    """Reference original segments from all three phones; do not crop or transcode."""
    t = event["utc_ms_estimate"]
    references: dict[str, list[dict]] = {}
    for device in device_ids:
        if not VALID_ID.fullmatch(device):
            raise ValueError("Unsafe device name")
        records = []
        folder = data_root / device
        if folder.is_symlink():
            raise RuntimeError("Symlink camera directory forbidden")
        if folder.is_dir():
            for sidecar in sorted(folder.glob("*.json")):
                if sidecar.is_symlink():
                    continue
                try:
                    meta = json.loads(sidecar.read_text(encoding="utf-8"))
                    start, end = meta.get("start_utc_ms_estimate"), meta.get("end_utc_ms_estimate")
                    seg_id = meta.get("segment_id", "")
                    digest = meta.get("sha256", "")
                    if not VALID_ID.fullmatch(seg_id) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                        continue
                    if ingested is not None and (device, seg_id, digest) not in ingested:
                        continue
                    source_video = folder / f"{seg_id}.mp4"
                    if source_video.is_symlink() or not source_video.is_file():
                        continue
                    own_source = any(c["device_id"] == device and
                                     c["segment_id"] == seg_id for c in event["candidates"])
                    # Own original must survive missing/unreliable wall-clock fields.
                    # Other cameras need plausible times before being suggested.
                    clock_valid = type(start) is int and type(end) is int and 0 <= start <= end
                    if not own_source and (t is None or not clock_valid or
                                           not (start <= t + half_window_ms and
                                                end >= t - half_window_ms)):
                        continue
                    records.append({"segment_id": seg_id, "sha256": digest,
                                    "start_utc_ms_estimate": start if clock_valid else None,
                                    "end_utc_ms_estimate": end if clock_valid else None})
                except (ValueError, OSError, KeyError, TypeError):
                    continue
        references[device] = records
    return {**event, "source_context": references, "preview_generated": False,
            "scientific_utc_verified": False, "archive_verified": False,
            "retention_permission": False}


def build_event_report(db: sqlite3.Connection, data_root: Path,
                       devices: list[str], window_ms: int = 2000) -> dict:
    candidates = load_candidates(db, data_root)
    provisional = group_provisional(candidates, window_ms)
    ingested = set(db.execute("""SELECT device_id, segment_id, sha256 FROM segments
                                 WHERE state='INGEST_VERIFIED'""").fetchall())
    return {
        "schema": "meteormesh.event.provisional.v0.2",
        "detector_candidates_unreviewed": len(candidates),
        "provisional_groups": len(provisional),
        "time_note": "Recorder wall-clock offsets and video PTS are NOT synchronized or validated",
        "auto_delete": False,
        "events": [context_for_event(event, data_root, devices,
                                     ingested=ingested) for event in provisional],
    }
