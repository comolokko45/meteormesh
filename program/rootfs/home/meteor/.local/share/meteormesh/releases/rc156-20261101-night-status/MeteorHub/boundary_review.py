#!/usr/bin/env python3
"""MeteorMesh 0.3: safe, derivative-only reviews of consecutive MP4 boundaries.

Source recordings are never edited, moved or deleted. Joining excerpts can
NEVER recover frames lost at a stop/restart seam or establish temporal continuity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

from hub_workflow import METDETPY_VERSION, sha256_file, verify_metdet_repo
from meteormesh_hub import verify_with_ffprobe
from meteornode_pull import VALID_ID


SHA256 = re.compile(r"^[a-f0-9]{64}$")
MAX_CLOCK_GAP_MS = 30_000
MIN_CLOCK_GAP_MS = -10_000


from mm01_bridge import guarded_subprocess_output as _mm01_process_output, guarded_db_connect as _mm01_db, verify_write_path as _mm01_verify, is_unsafe_symlink as _mm01_unsafe_link, guarded_partial_open as _mm01_open_partial, guarded_output_dir as _mm01_output_dir, guarded_detector_run as _mm01_detector_run, guarded_publish as _mm01_publish, is_protected_write_context as _mm01_active
def _verified_records(db: sqlite3.Connection, root: Path) -> dict[str, list[dict]]:
    rows = db.execute("""SELECT device_id, segment_id, sha256, size_bytes
                         FROM segments WHERE state='INGEST_VERIFIED'
                         ORDER BY device_id, segment_id""").fetchall()
    records: dict[str, list[dict]] = {}
    for device, segment, digest, size in rows:
        if not VALID_ID.fullmatch(device) or not VALID_ID.fullmatch(segment):
            raise ValueError("Unsafe device or segment name in SQLite")
        if not SHA256.fullmatch(digest):
            raise ValueError("Unsafe digest in SQLite")
        folder = root / device
        metadata_path = folder / f"{segment}.json"
        video = folder / f"{segment}.mp4"
        if folder.is_symlink() or metadata_path.is_symlink() or video.is_symlink():
            raise RuntimeError("Symlink input forbidden")
        if not metadata_path.is_file() or not video.is_file():
            raise RuntimeError(f"Verified source is missing: {device}/{segment}")
        if video.stat().st_size != size:
            raise RuntimeError(f"Verified video size changed: {device}/{segment}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("segment_id") != segment or metadata.get("sha256") != digest:
            raise RuntimeError(f"Verified sidecar identity changed: {device}/{segment}")
        start = metadata.get("start_utc_ms_estimate")
        end = metadata.get("end_utc_ms_estimate")
        if (type(start) is not int or type(end) is not int or
                start < 0 or end <= start):
            raise RuntimeError(f"Missing/bad approximate times: {device}/{segment}")
        records.setdefault(device, []).append({
            "device_id": device, "segment_id": segment, "sha256": digest,
            "start_ms_estimate": start, "end_ms_estimate": end,
            "video_path": str(video), "sidecar_path": str(metadata_path),
        })
    for segments in records.values():
        segments.sort(key=lambda s: (s["start_ms_estimate"], s["segment_id"]))
    return records


def plan_boundaries(db: sqlite3.Connection, root: Path, seconds: float = 2.0) -> dict:
    if not 0.5 <= seconds <= 5.0:
        raise ValueError("Review excerpt length must be 0.5..5 seconds per source")
    all_pairs = []
    cameras = _verified_records(db, root)
    for device, records in cameras.items():
        for left, right in zip(records, records[1:]):
            # Clock times in 0.1 are APPROXIMATE, never synchronized PTS.
            gap = right["start_ms_estimate"] - left["end_ms_estimate"]
            reviewable = MIN_CLOCK_GAP_MS <= gap <= MAX_CLOCK_GAP_MS
            stable = (f"{device}|{left['segment_id']}|{left['sha256']}|"
                      f"{right['segment_id']}|{right['sha256']}|{seconds:.3f}")
            all_pairs.append({
                "pair_id": "b_" + hashlib.sha256(stable.encode()).hexdigest()[:24],
                "device_id": device,
                "left": left,
                "right": right,
                "estimated_seam_gap_ms": gap,
                "reviewable": reviewable,
                "decision": ("APPROXIMATE_NEIGHBORS" if reviewable else
                             "NOT_STITCHED_UNCERTAIN_OR_LONG_CLOCK_GAP"),
                "seconds_per_side_requested": seconds,
                "seam_continuity_proven": False,
            })
    return {"schema": "meteormesh.boundary.plan.v0.3", "clock_quality": "UNVERIFIED",
            "source_videos_modified": False, "retention_permission": False,
            "pairs": all_pairs, "eligible_pairs": sum(p["reviewable"] for p in all_pairs)}


def _probe_video(path: Path) -> dict:
    executable = shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("ffprobe is required")
    args = [executable, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "format=duration:stream=width,height,codec_type",
            "-of", "json", str(path)]
    completed = subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)
    if completed.returncode:
        raise RuntimeError("ffprobe rejected source video")
    doc = json.loads(completed.stdout)
    video = next((s for s in doc.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError("Video stream is missing")
    duration = float(doc.get("format", {}).get("duration", 0))
    if duration <= 0:
        raise RuntimeError("Invalid video duration")
    width, height = video.get("width"), video.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width < 16 or height < 16:
        raise RuntimeError("Invalid video dimensions")
    return {"duration_seconds": duration, "width": width, "height": height}


def _read_existing_manifest(path: Path, video: Path) -> dict | None:
    if not path.exists() and not video.exists():
        return None
    if path.is_symlink() or video.is_symlink():
        raise RuntimeError("Unsafe existing review output")
    if not path.is_file() or not video.is_file():
        raise RuntimeError("Incomplete review output; manual recovery required")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("derived_sha256") != sha256_file(video):
        raise RuntimeError("Existing review video SHA-256 does not match manifest")
    return document


def build_review(pair: dict, outdir: Path, seconds: float, *,
                 run=subprocess.run, hash_cache: dict | None = None) -> dict:
    """Create near-lossless *derived* review MP4, never a replacement original."""
    _mm01_verify(outdir)
    if not pair["reviewable"]:
        raise ValueError("Refusing to stitch non-adjacent estimated timestamps")
    if _mm01_unsafe_link(outdir):
        raise RuntimeError("Output directory must not be a symlink")
    outdir = _mm01_output_dir(outdir)
    video = outdir / f"{pair['pair_id']}.mp4"
    manifest = outdir / f"{pair['pair_id']}.manifest.json"
    old = _read_existing_manifest(manifest, video)
    if old is not None:
        if old.get("pair_id") != pair["pair_id"]:
            raise RuntimeError("Existing manifest identity conflict")
        for side in ("left", "right"):
            source = pair[side]
            path = Path(source["video_path"])
            if path.is_symlink() or not path.is_file() or sha256_file(path) != source["sha256"]:
                raise RuntimeError("Original source changed after review was created")
        return old

    cache = hash_cache if hash_cache is not None else {}
    sources = []
    for side in ("left", "right"):
        source = pair[side]
        path = Path(source["video_path"])
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Unsafe/missing original MP4")
        digest = cache.get(path)
        if digest is None:
            digest = sha256_file(path)
            cache[path] = digest
        if digest != source["sha256"]:
            raise RuntimeError(f"Original SHA-256 changed: {source['segment_id']}")
        if verify_with_ffprobe(path)[0] != "INGEST_VERIFIED":
            raise RuntimeError(f"Original video failed ffprobe: {source['segment_id']}")
        sources.append(_probe_video(path))

    if (sources[0]["width"], sources[0]["height"]) != (sources[1]["width"], sources[1]["height"]):
        raise RuntimeError("Camera resolution changed at boundary; manual review required")
    leftlen = min(seconds, sources[0]["duration_seconds"])
    rightlen = min(seconds, sources[1]["duration_seconds"])
    if min(leftlen, rightlen) < 0.25:
        raise RuntimeError("Too few frames to review at seam")
    leftstart = max(0.0, sources[0]["duration_seconds"] - leftlen)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required")
    temporary = outdir / f".{pair['pair_id']}.{uuid.uuid4().hex}.pending.mp4"
    filter_desc = (f"[0:v:0]trim=duration={leftlen:.6f},setpts=PTS-STARTPTS[a];"
                   f"[1:v:0]trim=duration={rightlen:.6f},setpts=PTS-STARTPTS[b];"
                   f"[a][b]concat=n=2:v=1:a=0[v]")
    argv = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-ss", f"{leftstart:.6f}", "-i", pair["left"]["video_path"],
            "-i", pair["right"]["video_path"],
            "-filter_complex", filter_desc, "-map", "[v]", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary)]
    try:
        result = _mm01_process_output(run, argv, temporary, timeout=300)
        if result.returncode:
            raise RuntimeError(f"ffmpeg review export failed: {result.stderr[-500:]}")
        if not temporary.is_file() or temporary.is_symlink():
            raise RuntimeError("Expected review MP4 was not produced")
        info = _probe_video(temporary)
        expected_length = leftlen + rightlen
        if abs(info["duration_seconds"] - expected_length) > 0.25:
            raise RuntimeError("Derived review clip duration differs from requested windows")
        # hardlink is atomic create-only: existing review MP4 is NEVER overwritten.
        _mm01_verify(temporary)
        _mm01_verify(video)
        os.link(temporary, video)
        temporary.unlink()
        doc = {
            "schema": "meteormesh.boundary.review.v0.3",
            "pair_id": pair["pair_id"], "device_id": pair["device_id"],
            "source_segments": [
                {"segment_id": pair[side]["segment_id"], "sha256": pair[side]["sha256"]}
                for side in ("left", "right")
            ],
            "source_video_unchanged": True, "analysis_only": True,
            "derived_video": video.name, "derived_sha256": sha256_file(video),
            "derived_transcoded": True, "encoder": "libx264 CRF14",
            "left_original_tail_start_sec_estimate": round(leftstart, 6),
            "left_excerpt_seconds_requested": leftlen,
            "right_original_head_seconds_requested": rightlen,
            "derived_duration_seconds": info["duration_seconds"],
            "estimated_seam_gap_ms": pair["estimated_seam_gap_ms"],
            "seam_continuity_proven": False,
            "scientific_utc_verified": False,
            "warning": "Video seam is artificially joined for review. Lost original frames cannot be recovered.",
            "archive_verified": False, "retention_permission": False,
        }
        _mm01_verify(manifest)
        with manifest.open("x", encoding="utf-8") as output:
            json.dump(doc, output, indent=2)
            output.write("\n")
        return doc
    except Exception:
        # Do not delete a potentially useful diagnostic, any original video,
        # or an already-published output. Pending temp files are manual-only.
        raise


def run_boundary_detector(manifest_file: Path, repo: Path, config: Path,
                          *, command=subprocess.run) -> dict:
    """Optional MetDetPy review pass; never promotes a match to science-grade."""
    _mm01_verify(manifest_file.parent)
    if manifest_file.is_symlink():
        raise RuntimeError("Unsafe review manifest")
    meta = json.loads(manifest_file.read_text(encoding="utf-8"))
    if meta.get("schema") != "meteormesh.boundary.review.v0.3":
        raise RuntimeError("Unsupported review manifest")
    clip = manifest_file.parent / meta["derived_video"]
    if clip.is_symlink() or sha256_file(clip) != meta["derived_sha256"]:
        raise RuntimeError("Derivative has changed")
    repo = repo.resolve()
    config = config.resolve(strict=True)
    commit = verify_metdet_repo(repo)
    config_sha = sha256_file(config)
    detection_output = manifest_file.with_suffix(".mdrf.json")
    audit_path = manifest_file.with_suffix(".analysis.json")
    if detection_output.exists() or audit_path.exists():
        raise RuntimeError("Review detection already present; refusing overwrite")
    temporary = manifest_file.parent / f".{uuid.uuid4().hex}.pending.json"
    argv = [sys.executable, str(repo / "MetDetPy.py"), str(clip.resolve()),
            "--cfg", str(config), "--mode", "backend", "--save-path", str(temporary)]
    result = _mm01_process_output(command, argv, temporary, timeout=1200, cwd=repo)
    if result.returncode:
        raise RuntimeError(f"MetDetPy boundary pass failed: {result.stderr[-600:]}")
    data = json.loads(temporary.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise RuntimeError("Detector output is not valid MDRF JSON")
    _mm01_verify(temporary)
    _mm01_verify(detection_output)
    os.link(temporary, detection_output)
    temporary.unlink()
    count = sum(len(r.get("target", [])) for r in data["results"] if isinstance(r, dict)
                and isinstance(r.get("target", []), list))
    audit = {"schema": "meteormesh.boundary.analysis.v0.3",
             "pair_id": meta["pair_id"], "candidate_count_unreviewed": count,
             "results_file": detection_output.name,
             "result_sha256": sha256_file(detection_output),
             "detector_version": METDETPY_VERSION, "detector_commit": commit,
             "config_sha256": config_sha, "input_sha256": meta["derived_sha256"],
             "verified_same_meteor": False, "scientific_utc_verified": False,
             "retention_permission": False}
    _mm01_verify(audit_path)
    with audit_path.open("x", encoding="utf-8") as output:
        json.dump(audit, output, indent=2)
        output.write("\n")
    return audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=2)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="List safe and unsafe approximate MP4 neighbors")
    plan.add_argument("--output", required=True, type=Path)
    build = sub.add_parser("build", help="Generate short analysis-only seam review clips")
    build.add_argument("--output-dir", required=True, type=Path)
    build.add_argument("--limit", type=int, default=1)
    detect = sub.add_parser("detect", help="Run pinned MetDetPy on built seam excerpts")
    detect.add_argument("--review-dir", required=True, type=Path)
    detect.add_argument("--metdet-repo", required=True, type=Path)
    detect.add_argument("--metdet-config", required=True, type=Path)
    detect.add_argument("--limit", type=int, default=1)
    args = parser.parse_args(argv)
    # MM01_GUARDED_CLI_V0_4
    from contextlib import nullcontext as _mm01_nullcontext
    from mm01_bridge import guarded_cli as _mm01_guarded_cli
    _mm01_mode = 'boundary'
    with (_mm01_guarded_cli(args, mode=_mm01_mode) if _mm01_mode else _mm01_nullcontext()):
        if _mm01_unsafe_link(args.data_dir):
            raise RuntimeError("Data directory cannot be a symlink")
        dbpath = args.data_dir / "meteorhub.sqlite3"
        if dbpath.is_symlink() or not dbpath.is_file():
            raise RuntimeError("Verified-ingest database must exist")
        if args.command != "plan" and not 1 <= args.limit <= 500:
            raise ValueError("Limit must be between 1 and 500")
        if args.command == "detect":
            if _mm01_unsafe_link(args.review_dir):
                raise RuntimeError("Review directory cannot be a symlink")
            results = []
            for manifest in sorted(args.review_dir.glob("*.manifest.json")):
                if len(results) >= args.limit:
                    break
                audit_path = manifest.with_suffix(".analysis.json")
                if audit_path.is_file():
                    continue
                results.append(run_boundary_detector(manifest, args.metdet_repo, args.metdet_config))
            print(json.dumps({"analyses": results, "source_deleted": False}))
            return 0
        from meteor_agent import read_only_connection
        with read_only_connection(dbpath) as db:
            plan_data = plan_boundaries(db, args.data_dir, args.seconds)
        if args.command == "plan":
            _mm01_verify(args.output)
            with args.output.open("x", encoding="utf-8") as output:
                json.dump(plan_data, output, ensure_ascii=False, indent=2)
                output.write("\n")
            print(json.dumps({"pairs": len(plan_data["pairs"]),
                              "eligible_pairs": plan_data["eligible_pairs"]}))
        elif args.command == "build":
            completed = []
            cache = {}
            for pair in plan_data["pairs"]:
                if pair["reviewable"]:
                    completed.append(build_review(pair, args.output_dir, args.seconds, hash_cache=cache))
                if len(completed) >= args.limit:
                    break
            print(json.dumps({"reviews": [i["pair_id"] for i in completed],
                              "source_deleted": False}))
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, FileNotFoundError, FileExistsError,
            sqlite3.Error, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"MeteorMesh boundary review: {exc}", file=sys.stderr)
        sys.exit(2)
