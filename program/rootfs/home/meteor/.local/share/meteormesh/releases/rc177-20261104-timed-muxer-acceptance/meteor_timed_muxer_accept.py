#!/usr/bin/env python3
"""Read-only acceptance check for consecutive MeteorNode timed-muxer MP4 files."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_SIDECAR_BYTES = 256 * 1024
DURATION_MODE = "MEDIACODEC_PTS_DURATION_SYNC_FRAME"
EMERGENCY_MODE = "MEDIACODEC_EMERGENCY_SIZE_SYNC_FRAME"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def integer(value: Any) -> int | None:
    return value if type(value) is int else None


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


@dataclass(frozen=True)
class Segment:
    sidecar: Path
    video: Path
    metadata: dict[str, Any]

    @property
    def segment_id(self) -> str:
        return str(self.metadata.get("segment_id", self.sidecar.stem))

    @property
    def start_us(self) -> int | None:
        return integer(self.metadata.get("media_presentation_start_us"))

    @property
    def end_us(self) -> int | None:
        return integer(self.metadata.get("media_presentation_end_us"))


def load_segments(folder: Path, expected_version: str, device_id: str | None) -> tuple[list[Segment], list[str]]:
    found: list[Segment] = []
    errors: list[str] = []
    for sidecar in sorted(folder.glob("*.json")):
        if sidecar.is_symlink() or not sidecar.is_file():
            continue
        try:
            if sidecar.stat().st_size > MAX_SIDECAR_BYTES:
                errors.append(f"{sidecar.name}:SIDECAR_TOO_LARGE")
                continue
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            errors.append(f"{sidecar.name}:SIDECAR_READ_ERROR")
            continue
        if not isinstance(metadata, dict) or metadata.get("app_version") != expected_version:
            continue
        if device_id and metadata.get("device_id") != device_id:
            continue
        segment_id = metadata.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id or Path(segment_id).name != segment_id:
            errors.append(f"{sidecar.name}:INVALID_SEGMENT_ID")
            continue
        video = folder / f"{segment_id}.mp4"
        if video.is_symlink() or not video.is_file():
            errors.append(f"{segment_id}:MP4_MISSING")
            continue
        found.append(Segment(sidecar, video, metadata))
    found.sort(key=lambda item: (item.start_us is None, item.start_us or 0, item.segment_id))
    return found, errors


def ffprobe_packets(ffprobe: str, video: Path) -> tuple[dict[str, Any] | None, str | None]:
    command = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,avg_frame_rate,duration,nb_frames:"
        "format=duration,size:packet=pts_time,dts_time,duration_time,flags",
        "-show_streams", "-show_format", "-show_packets", "-of", "json", str(video),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None, "FFPROBE_UNAVAILABLE_OR_TIMEOUT"
    if result.returncode != 0:
        return None, "FFPROBE_FAILED"
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "FFPROBE_INVALID_JSON"
    return parsed if isinstance(parsed, dict) else None, None


def inspect_segment(segment: Segment, ffprobe: str) -> dict[str, Any]:
    metadata = segment.metadata
    failures: list[str] = []
    expected_sha = metadata.get("sha256")
    actual_sha = sha256(segment.video)
    if expected_sha != actual_sha:
        failures.append("SHA256_MISMATCH")
    if integer(metadata.get("size_bytes")) != segment.video.stat().st_size:
        failures.append("SIZE_MISMATCH")
    if metadata.get("segmentation") == EMERGENCY_MODE:
        failures.append("EMERGENCY_SIZE_ROTATION_OBSERVED")
    if metadata.get("segmentation") != DURATION_MODE:
        failures.append("NOT_DURATION_ROTATION")
    if metadata.get("segment_boundary_verified") is not False:
        failures.append("UNEXPECTED_PHONE_BOUNDARY_CLAIM")

    start_us, end_us = segment.start_us, segment.end_us
    target_ms = integer(metadata.get("segment_duration_target_ms"))
    span_s = (end_us - start_us) / 1_000_000 if start_us is not None and end_us is not None else None
    if start_us is None or end_us is None or end_us <= start_us:
        failures.append("INVALID_GLOBAL_PTS_RANGE")
    elif target_ms is None or not (target_ms / 1000 - 3.0 <= span_s <= target_ms / 1000 + 5.0):
        failures.append("PTS_DURATION_OUTSIDE_TARGET_TOLERANCE")

    probe, probe_error = ffprobe_packets(ffprobe, segment.video)
    packet_count = 0
    first_keyframe = False
    pts_monotonic = False
    probe_duration = None
    first_pts = None
    if probe_error:
        failures.append(probe_error)
    elif probe is not None:
        packets = probe.get("packets") if isinstance(probe.get("packets"), list) else []
        pts = [number(packet.get("pts_time")) for packet in packets if isinstance(packet, dict)]
        pts = [value for value in pts if value is not None]
        packet_count = len(packets)
        first_pts = pts[0] if pts else None
        first_keyframe = bool(packets and isinstance(packets[0], dict) and "K" in str(packets[0].get("flags", "")))
        pts_monotonic = len(pts) == packet_count and all(b > a for a, b in zip(pts, pts[1:]))
        formats = probe.get("format") if isinstance(probe.get("format"), dict) else {}
        probe_duration = number(formats.get("duration"))
        if packet_count < 2:
            failures.append("TOO_FEW_PACKETS")
        if not first_keyframe:
            failures.append("FIRST_PACKET_NOT_KEYFRAME")
        if not pts_monotonic:
            failures.append("PACKET_PTS_NOT_STRICTLY_MONOTONIC")
        if first_pts is None or abs(first_pts) > 0.1:
            failures.append("FIRST_PACKET_PTS_NOT_NEAR_ZERO")
        if target_ms is None or probe_duration is None or not (
            target_ms / 1000 - 3.0 <= probe_duration <= target_ms / 1000 + 5.0
        ):
            failures.append("FFPROBE_DURATION_OUTSIDE_TARGET_TOLERANCE")

    return {
        "segment_id": segment.segment_id,
        "video": str(segment.video),
        "segmentation": metadata.get("segmentation"),
        "app_version": metadata.get("app_version"),
        "app_version_code": metadata.get("app_version_code"),
        "sha256_verified": expected_sha == actual_sha,
        "global_pts_start_us": start_us,
        "global_pts_end_us": end_us,
        "global_pts_span_seconds": span_s,
        "ffprobe_duration_seconds": probe_duration,
        "packet_count": packet_count,
        "first_packet_keyframe": first_keyframe,
        "packet_pts_strictly_monotonic": pts_monotonic,
        "first_packet_pts_seconds": first_pts,
        "failures": failures,
        "pass": not failures,
    }


def inspect_boundary(previous: Segment, following: Segment) -> dict[str, Any]:
    failures: list[str] = []
    prev_end, next_start = previous.end_us, following.start_us
    fps = integer(previous.metadata.get("nominal_fps")) or 30
    maximum_gap_us = max(100_000, int(3_000_000 / max(1, fps)))
    global_gap_us = next_start - prev_end if prev_end is not None and next_start is not None else None
    if global_gap_us is None or not (0 < global_gap_us <= maximum_gap_us):
        failures.append("GLOBAL_PTS_BOUNDARY_GAP_OUT_OF_RANGE")
    prev_end_utc = integer(previous.metadata.get("end_utc_ms_estimate"))
    next_start_utc = integer(following.metadata.get("start_utc_ms_estimate"))
    utc_gap_ms = next_start_utc - prev_end_utc if prev_end_utc is not None and next_start_utc is not None else None
    if utc_gap_ms is None or abs(utc_gap_ms) > 50:
        failures.append("UTC_BOUNDARY_ESTIMATE_DISCONTINUITY")
    return {
        "previous_segment_id": previous.segment_id,
        "following_segment_id": following.segment_id,
        "global_pts_gap_us": global_gap_us,
        "maximum_allowed_gap_us": maximum_gap_us,
        "utc_boundary_gap_ms": utc_gap_ms,
        "failures": failures,
        "pass": not failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--device-id")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--min-duration-segments", type=int, default=2)
    args = parser.parse_args()
    if not args.folder.is_dir() or args.folder.is_symlink():
        parser.error("folder must be a real directory")
    if not 2 <= args.min_duration_segments <= 20:
        parser.error("min-duration-segments must be 2..20")

    segments, load_errors = load_segments(args.folder, args.expected_version, args.device_id)
    duration_segments = [item for item in segments if item.metadata.get("segmentation") == DURATION_MODE]
    emergency_segments = [item.segment_id for item in segments if item.metadata.get("segmentation") == EMERGENCY_MODE]
    selected = duration_segments[-args.min_duration_segments:]
    inspected = [inspect_segment(item, args.ffprobe) for item in selected]
    boundaries = [inspect_boundary(a, b) for a, b in zip(selected, selected[1:])]
    enough = len(duration_segments) >= args.min_duration_segments
    passed = enough and not load_errors and not emergency_segments and all(x["pass"] for x in inspected + boundaries)
    status = "PASS" if passed else "PENDING" if not enough and not load_errors and not emergency_segments else "FAIL"
    report = {
        "schema": "meteormesh.timed-muxer-acceptance.v1",
        "status": status,
        "read_only": True,
        "expected_version": args.expected_version,
        "device_id": args.device_id,
        "folder": str(args.folder),
        "matching_segments": len(segments),
        "duration_segments": len(duration_segments),
        "required_duration_segments": args.min_duration_segments,
        "emergency_segments": emergency_segments,
        "load_errors": load_errors,
        "segments": inspected,
        "boundaries": boundaries,
        "boundary_verified_by_g62": passed,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 2 if status == "PENDING" else 1


if __name__ == "__main__":
    sys.exit(main())
