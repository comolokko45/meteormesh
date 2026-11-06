#!/usr/bin/env python3
"""Opt-in meteor experiment tools; never part of the nightly critical path.

Uses existing ffmpeg/ffprobe and the pinned MetDetPy checkout. No AI model,
web server, cloud, original-video mutation, or retention operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from hub_workflow import METDETPY_VERSION, verify_metdet_repo
from meteormesh_hub import verify_with_ffprobe


from mm01_bridge import guarded_subprocess_output as _mm01_process_output, guarded_db_connect as _mm01_db, verify_write_path as _mm01_verify, is_unsafe_symlink as _mm01_unsafe_link, guarded_partial_open as _mm01_open_partial, guarded_output_dir as _mm01_output_dir, guarded_detector_run as _mm01_detector_run, guarded_publish as _mm01_publish, is_protected_write_context as _mm01_active
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def checked_video(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing or unsafe input video")
    status, info = verify_with_ffprobe(path)
    if status != "INGEST_VERIFIED" or not info:
        raise RuntimeError("Video is not validated by ffprobe")
    streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not streams or not isinstance(streams[0].get("width"), int) or (
            not isinstance(streams[0].get("height"), int)):
        raise ValueError("ffprobe does not expose video dimensions")
    return {"width": streams[0]["width"], "height": streams[0]["height"],
            "duration_seconds": float(info["format"]["duration"])}


def mask_info(path: Path, width: int, height: int) -> dict:
    if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".png":
        raise ValueError("Mask must be an existing non-symlink PNG")
    if path.stat().st_size > 100 * 1024**2:
        raise ValueError("Mask PNG is oversized")
    with path.open("rb") as stream:
        header = stream.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError("Mask PNG header is invalid")
    actual = struct.unpack(">II", header[16:24])
    if actual != (width, height):
        raise ValueError(f"Mask dimensions {actual} do not match video {(width, height)}")
    # A valid-looking 24-byte PNG header is not an image. Let the already
    # required ffmpeg decode a full frame before claiming the mask is usable.
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg required to validate actual PNG image data")
    outcome = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                              "-i", str(path), "-frames:v", "1", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=45, check=False)
    if outcome.returncode or outcome.stderr.strip():
        raise ValueError("Mask PNG cannot be decoded cleanly")
    return {"width": width, "height": height, "sha256": sha256(path),
            "mask_path": str(path), "mask_decoded": True,
            "mask_applied_to_original": False}


def planned_detection(video: Path, config: Path, mask: Path | None,
                      sensitivity: str | None = None,
                      resize: str | None = None) -> dict:
    if config.is_symlink() or not config.is_file():
        raise ValueError("Missing/unsafe MetDetPy configuration")
    if sensitivity is not None and sensitivity not in ("low", "normal", "high"):
        raise ValueError("Unsupported sensitivity")
    if resize is not None and not (
            resize.isascii() and resize.isdigit() and 128 <= int(resize) <= 4096):
        raise ValueError("Resize must be long-side integer between 128 and 4096")
    source = checked_video(video)
    details = {"source": str(video), "source_sha256": sha256(video),
               "video": source, "detector_version": METDETPY_VERSION,
               "config_sha256": sha256(config),
               "mask": mask_info(mask, source["width"], source["height"]) if mask else None,
               "sensitivity": sensitivity, "resize": resize,
               "run_is_optional_experiment": True, "retention_permission": False,
               "recording_mutated": False}
    return details


def detector_command(repo: Path, video: Path, config: Path, output: Path,
                     mask: Path | None, sensitivity: str | None,
                     resize: str | None) -> list[str]:
    argv = [sys.executable, str(repo / "MetDetPy.py"), str(video),
            "--cfg", str(config), "--mode", "backend", "--save-path", str(output)]
    if mask:
        argv.extend(["--mask", str(mask)])
    if sensitivity:
        argv.extend(["--sensitivity", sensitivity])
    if resize:
        argv.extend(["--resize", resize])
    return argv


def _check_output(path: Path) -> None:
    if _mm01_unsafe_link(path.parent) or not path.parent.is_dir():
        raise ValueError("Output parent must be existing safe directory")
    if path.exists() or path.is_symlink():
        raise FileExistsError("Output already exists; refusing overwrite")


def detect_sample(*, repo: Path, video: Path, config: Path,
                  output: Path, mask: Path | None = None,
                  sensitivity: str | None = None,
                  resize: str | None = None, timeout: int = 1800,
                  run_command=subprocess.run) -> dict:
    """Produce a separate lab MDRF file plus provenance manifest, both no-clobber."""
    _mm01_verify(output)
    if not 60 <= timeout <= 6 * 3600:
        raise ValueError("Invalid detector timeout")
    # Resolve AFTER validating raw paths against symlink inputs. The detector
    # runs with cwd=repo; otherwise relative video/mask paths would break.
    if any(path.is_symlink() for path in (video, config, repo) if path is not None) or (
            mask is not None and mask.is_symlink()):
        raise ValueError("Experiment source symlinks are forbidden")
    video = video.resolve(strict=True)
    config = config.resolve(strict=True)
    repo = repo.resolve(strict=True)
    mask = mask.resolve(strict=True) if mask else None
    output = output.absolute()
    _check_output(output)
    manifest_path = output.with_name(output.name + ".audit.json")
    _check_output(manifest_path)
    config_details = planned_detection(video, config, mask, sensitivity, resize)
    commit = verify_metdet_repo(repo)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".meteor-lab-") as folder:
        pending = Path(folder) / "output.json"
        argv = detector_command(repo, video, config, pending, mask, sensitivity, resize)
        start = time.monotonic()
        try:
            outcome = _mm01_process_output(run_command, argv, pending, timeout=timeout, cwd=repo)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Detector experiment timed out; source is untouched") from exc
        elapsed = time.monotonic() - start
        if outcome.returncode:
            raise RuntimeError(f"Detector exit {outcome.returncode}: {outcome.stderr[-600:]}")
        if pending.is_symlink() or not pending.is_file():
            raise RuntimeError("No safe detector output generated")
        result = json.loads(pending.read_text(encoding="utf-8"))
        if not isinstance(result, dict) or not isinstance(result.get("results"), list):
            raise RuntimeError("Unexpected MDRF JSON")
        # Never attach results to a source/config/mask that changed during run.
        if sha256(video) != config_details["source_sha256"] or (
                sha256(config) != config_details["config_sha256"]) or (
                mask and sha256(mask) != config_details["mask"]["sha256"]):
            raise RuntimeError("Experiment input changed during detector run; nothing published")
        audit = {**config_details, "detector_commit": commit,
                 "result_sha256": sha256(pending), "elapsed_seconds": round(elapsed, 3),
                 "processing_ratio_R": round(elapsed / config_details["video"]["duration_seconds"], 3),
                 "result_record_groups": len(result["results"]),
                 "scientific_recall": "UNVERIFIED_WITHOUT_HUMAN_LABELS"}
        temp_audit = Path(folder) / "audit.json"
        temp_audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
        # No-replace hard links; cleanup removes ONLY our private temporary files.
        _mm01_verify(pending)
        _mm01_verify(output)
        os.link(pending, output)
        _mm01_verify(temp_audit)
        _mm01_verify(manifest_path)
        os.link(temp_audit, manifest_path)
    return audit


def sampled_frame(video: Path, output: Path, at_seconds: float = 30,
                  run_command=subprocess.run) -> dict:
    _mm01_verify(output)
    output = output.absolute()
    _check_output(output)
    if output.suffix.lower() != ".jpg":
        raise ValueError("Single-frame sample output must use .jpg")
    source = checked_video(video)
    original_sha256 = sha256(video)
    if not isinstance(at_seconds, (float, int)) or not math.isfinite(at_seconds) or (
            at_seconds < 0 or at_seconds >= source["duration_seconds"]):
        raise ValueError("Sample timestamp must be inside the recording")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for sampled sky preview")
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".meteor-frame-") as folder:
        pending = Path(folder) / "frame.jpg"
        result = _mm01_process_output(run_command, [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss",
                             str(at_seconds), "-i", str(video), "-frames:v", "1",
                             "-q:v", "4", "-y", str(pending)], pending, timeout=120)
        if result.returncode or not pending.is_file() or pending.stat().st_size < 100:
            raise RuntimeError("Unable to extract sample frame")
        if sha256(video) != original_sha256:
            raise RuntimeError("Sample source changed during export; nothing published")
        _mm01_verify(pending)
        _mm01_verify(output)
        os.link(pending, output)
    return {"output": str(output), "source_sha256": original_sha256,
            "at_seconds": at_seconds, "scientific_quality_classification": "NOT_PERFORMED",
            "retention_permission": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("plan", "detect-sample"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--video", type=Path, required=True)
        cmd.add_argument("--config", type=Path, required=True)
        cmd.add_argument("--mask", type=Path)
        cmd.add_argument("--sensitivity", choices=("low", "normal", "high"))
        cmd.add_argument("--resize")
        if name == "detect-sample":
            cmd.add_argument("--repo", type=Path, required=True)
            cmd.add_argument("--output", type=Path, required=True)
    frame = sub.add_parser("sample-frame")
    frame.add_argument("--video", type=Path, required=True)
    frame.add_argument("--output", type=Path, required=True)
    frame.add_argument("--at-seconds", type=float, default=30)
    args = parser.parse_args()
    # MM01_GUARDED_CLI_V0_4
    from contextlib import nullcontext as _mm01_nullcontext
    from mm01_bridge import guarded_cli as _mm01_guarded_cli
    _mm01_mode = (None if args.action == 'plan' else 'lab-output')
    with (_mm01_guarded_cli(args, mode=_mm01_mode) if _mm01_mode else _mm01_nullcontext()):
        if args.action == "sample-frame":
            data = sampled_frame(args.video, args.output, args.at_seconds)
        elif args.action == "plan":
            data = planned_detection(args.video, args.config, args.mask,
                                     args.sensitivity, args.resize)
        else:
            data = detect_sample(repo=args.repo, video=args.video, config=args.config,
                                 output=args.output, mask=args.mask,
                                 sensitivity=args.sensitivity, resize=args.resize)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
