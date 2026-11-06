#!/usr/bin/env python3
"""Create reproducible, SHA-verified review manifests without moving video.

These manifests are NOT Drive archives, retention receipts, scientific proof of
one meteor, or replacements for the source MP4 files they reference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

from hub_workflow import sha256_file
from meteor_agent import read_only_connection
from meteornode_pull import VALID_ID


DIGEST = re.compile(r"^[a-f0-9]{64}$")


from mm01_bridge import guarded_db_connect as _mm01_db, verify_write_path as _mm01_verify, is_unsafe_symlink as _mm01_unsafe_link, guarded_partial_open as _mm01_open_partial, guarded_output_dir as _mm01_output_dir, guarded_detector_run as _mm01_detector_run, guarded_publish as _mm01_publish, is_protected_write_context as _mm01_active
def _stable_hash(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


class SourceVerifier:
    def __init__(self, db: sqlite3.Connection, data_root: Path):
        self.data_root = data_root
        self.verified = {(d, s, h): size for d, s, h, size in db.execute(
            "SELECT device_id, segment_id, sha256, size_bytes FROM segments "
            "WHERE state='INGEST_VERIFIED'")}
        self.sha_cache: dict[Path, str] = {}

    def check(self, device_id: str, segment_id: str, digest: str) -> dict:
        if (not isinstance(device_id, str) or not VALID_ID.fullmatch(device_id) or
                not isinstance(segment_id, str) or not VALID_ID.fullmatch(segment_id) or
                not isinstance(digest, str) or not DIGEST.fullmatch(digest)):
            raise ValueError("Unsafe or incomplete original source reference")
        expected_size = self.verified.get((device_id, segment_id, digest))
        if expected_size is None:
            raise RuntimeError(f"Source is not INGEST_VERIFIED: {device_id}/{segment_id}")
        folder = self.data_root / device_id
        video = folder / f"{segment_id}.mp4"
        sidecar = folder / f"{segment_id}.json"
        if folder.is_symlink() or video.is_symlink() or sidecar.is_symlink():
            raise RuntimeError("Unsafe symlink source")
        if not video.is_file() or not sidecar.is_file() or video.stat().st_size != expected_size:
            raise RuntimeError(f"Verified source missing/changed: {device_id}/{segment_id}")
        if video not in self.sha_cache:
            self.sha_cache[video] = sha256_file(video)
        if self.sha_cache[video] != digest:
            raise RuntimeError(f"Video hash changed: {device_id}/{segment_id}")
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        if meta.get("segment_id") != segment_id or meta.get("sha256") != digest:
            raise RuntimeError(f"Sidecar hash/identity mismatch: {device_id}/{segment_id}")
        return {"device_id": device_id, "segment_id": segment_id,
                "original_sha256": digest,
                "original_relpath": f"{device_id}/{segment_id}.mp4",
                "size_bytes": expected_size,
                "original_video_unchanged": True}


def _report_events(report_file: Path) -> list[dict]:
    if report_file.is_symlink():
        raise RuntimeError("Unsafe event report")
    data = json.loads(report_file.read_text(encoding="utf-8"))
    if data.get("schema") != "meteormesh.event.provisional.v0.2":
        raise ValueError("Expected MeteorHub 0.2 provisional event report")
    if data.get("auto_delete") is not False:
        raise ValueError("Unsafe event report deletion flag")
    events = data.get("events")
    if not isinstance(events, list):
        raise ValueError("Malformed provisional event report")
    return events


def normal_event_manifests(report_file: Path, verifier: SourceVerifier) -> list[dict]:
    packages = []
    for event in _report_events(report_file):
        identifier = event.get("provisional_event_id")
        if not isinstance(identifier, str) or not VALID_ID.fullmatch(identifier):
            raise RuntimeError("Unsafe provisional event id")
        if event.get("confirmed_same_meteor") is not False or event.get("retention_permission") is not False:
            raise RuntimeError("Event misrepresents science or retention authority")
        references = {}
        for candidate in event.get("candidates", []):
            item = verifier.check(candidate["device_id"], candidate["segment_id"],
                                  candidate["source_sha256"])
            references[(item["device_id"], item["segment_id"])] = item
        for device_id, items in event.get("source_context", {}).items():
            if not isinstance(items, list):
                raise ValueError("Invalid event context")
            for segment in items:
                item = verifier.check(device_id, segment["segment_id"], segment["sha256"])
                references[(item["device_id"], item["segment_id"])] = item
        if not references:
            raise RuntimeError("Review event has no original sources")
        packages.append({
            "package_id": identifier,
            "source_type": "NORMAL_SEGMENT_PROVISIONAL_CANDIDATE",
            "candidate_count": len(event.get("candidates", [])),
            "camera_ids": event.get("cameras", []),
            "source_videos": sorted(references.values(), key=lambda s: (s["device_id"], s["segment_id"])),
            "temporal_alignment": "UNVERIFIED_APPROXIMATE",
            "scientific_same_meteor_confirmed": False,
        })
    return packages


def boundary_event_manifests(folder: Path, verifier: SourceVerifier) -> list[dict]:
    if folder.is_symlink():
        raise RuntimeError("Unsafe boundary review directory")
    packages = []
    for audit_path in sorted(folder.glob("*.analysis.json")):
        if audit_path.is_symlink():
            raise RuntimeError("Unsafe boundary analysis manifest")
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if audit.get("schema") != "meteormesh.boundary.analysis.v0.3":
            raise RuntimeError("Unexpected boundary analysis schema")
        if not audit.get("candidate_count_unreviewed"):
            continue
        pair_id = audit["pair_id"]
        if not VALID_ID.fullmatch(pair_id):
            raise RuntimeError("Unsafe boundary identity")
        manifest = folder / f"{pair_id}.manifest.json"
        if manifest.is_symlink():
            raise RuntimeError("Unsafe boundary manifest")
        source = json.loads(manifest.read_text(encoding="utf-8"))
        if source.get("pair_id") != pair_id or source.get("retention_permission") is not False:
            raise RuntimeError("Boundary result/retention mismatch")
        if (source.get("derived_video") != f"{pair_id}.mp4" or
                audit.get("results_file") != f"{pair_id}.manifest.mdrf.json"):
            raise RuntimeError("Unexpected boundary derivative path")
        reviewed_video = folder / source["derived_video"]
        detector_json = folder / audit["results_file"]
        if (reviewed_video.is_symlink() or detector_json.is_symlink() or
                sha256_file(reviewed_video) != source["derived_sha256"] or
                sha256_file(detector_json) != audit["result_sha256"]):
            raise RuntimeError("Boundary derivative/result hash mismatch")
        original_refs = [verifier.check(source["device_id"], item["segment_id"], item["sha256"])
                         for item in source["source_segments"]]
        packages.append({
            "package_id": pair_id,
            "source_type": "BOUNDARY_DERIVED_REVIEW_CANDIDATE",
            "candidate_count": audit["candidate_count_unreviewed"],
            "camera_ids": [source["device_id"]],
            "source_videos": original_refs,
            "detector_result_sha256": audit["result_sha256"],
            "derived_video_sha256": source["derived_sha256"],
            "seam_continuity_proven": False,
            "temporal_alignment": "UNVERIFIED_ARTIFICIAL_SEAM",
            "scientific_same_meteor_confirmed": False,
        })
    return packages


def build_indexes(verifier: SourceVerifier, report_file: Path,
                  boundary_dir: Path | None = None) -> list[dict]:
    events = normal_event_manifests(report_file, verifier)
    if boundary_dir is not None:
        events.extend(boundary_event_manifests(boundary_dir, verifier))
    indexed = []
    seen = set()
    for event in events:
        if event["package_id"] in seen:
            raise RuntimeError("Duplicate event/boundary identifier")
        seen.add(event["package_id"])
        payload = {
            "schema": "meteormesh.event.source-index.v0.3",
            **event,
            "archived_originals": False,
            "drive_verified": False,
            "originals_copied_to_package": False,
            "preview_generated": False,
            "retention_permission": False,
            "warning": "Reference index only. Keep original MP4s; this is not an archive or purge receipt.",
        }
        indexed.append({**payload, "content_sha256": _stable_hash(payload)})
    return indexed


def save_indexes(indexes: list[dict], output_dir: Path) -> list[str]:
    _mm01_verify(output_dir)
    if _mm01_unsafe_link(output_dir):
        raise RuntimeError("Index output directory must not be a symlink")
    output_dir = _mm01_output_dir(output_dir)
    # Preflight avoids partial output due to already-existing files.
    paths = [output_dir / f"{event['package_id']}.index.json" for event in indexes]
    if any(path.exists() or path.is_symlink() for path in paths):
        raise FileExistsError("Review index exists; refusing to overwrite")
    for path, payload in zip(paths, indexes):
        _mm01_verify(path)
        with path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    return [str(p) for p in paths]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--boundary-review-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    # MM01_GUARDED_CLI_V0_4
    from contextlib import nullcontext as _mm01_nullcontext
    from mm01_bridge import guarded_cli as _mm01_guarded_cli
    _mm01_mode = 'event-index'
    with (_mm01_guarded_cli(args, mode=_mm01_mode) if _mm01_mode else _mm01_nullcontext()):
        if _mm01_unsafe_link(args.data_dir):
            raise RuntimeError("Data directory must not be a symlink")
        with read_only_connection(args.data_dir / "meteorhub.sqlite3") as conn:
            verifier = SourceVerifier(conn, args.data_dir)
            indexes = build_indexes(verifier, args.events, args.boundary_review_dir)
        paths = save_indexes(indexes, args.output_dir)
        print(json.dumps({"manifests": paths, "source_files_copied": False,
                          "retention_permission": False}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, FileNotFoundError, FileExistsError,
            sqlite3.Error, OSError, json.JSONDecodeError) as exc:
        print(f"MeteorMesh event index: {exc}", file=sys.stderr)
        sys.exit(2)
