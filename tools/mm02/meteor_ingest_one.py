#!/usr/bin/env python3
"""MM-02: bounded, guarded MeteorNode 0.10 -> HDD -> SQLite -> detector queue.

Downloads at most ONE selected READY segment, with no phone commands, deletes,
cloud sync, or detector run. This is a manually invoked integration checkpoint;
not a background service. HTTP is explicitly lab-only and RFC1918-restricted.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import sqlite3
from pathlib import Path

from hub_workflow import METDETPY_VERSION, queue_summary, utc_now
import hashlib
from meteormesh_hub import (DEVICE_ID, create_database, safely_prepare_folder,
                            store_verified_segment, verify_with_ffprobe)
from meteormesh_pipeline import sha256_file
from meteornode_pull import MeteorNode, MeteorNodeLabHTTP, VALID_ID
from storage_guard import ensure_destination, ensure_production_root

DEFAULT_ROOT = Path('/srv/meteor-data')
TOKEN_ENV = 'METEOR_NODE_VIVA_TEST_TOKEN'
RESERVE_BYTES = 20 * 1024 ** 3
MIN_VIDEO_BYTES = 50 * 1024 ** 2


def select_segment(document: dict, *, specific: str | None = None,
                   min_size: int = MIN_VIDEO_BYTES) -> dict:
    """Select the largest READY video, not an early tiny/aborted test file."""
    if not isinstance(document, dict) or not isinstance(document.get('segments'), list):
        raise ValueError('Invalid phone segment inventory')
    eligible = []
    for segment in document['segments']:
        if not isinstance(segment, dict):
            continue
        ident = segment.get('segment_id')
        size = segment.get('size_bytes')
        digest = segment.get('sha256')
        state = segment.get('state', segment.get('status', 'READY'))
        if not (isinstance(ident, str) and VALID_ID.fullmatch(ident)
                and type(size) is int and size >= min_size
                and isinstance(digest, str)
                and re.fullmatch(r'[0-9a-fA-F]{64}', digest)
                and str(state).upper() == 'READY'):
            continue
        if specific is None or specific == ident:
            eligible.append(segment)
    if not eligible:
        raise RuntimeError('NO_ELIGIBLE_READY_SEGMENT: no qualifying video; no download')
    return max(eligible, key=lambda item: (item['size_bytes'], item['segment_id']))


@contextlib.contextmanager
def exclusive_ingest_lock(root: Path):
    """Prevent two copies of this bounded-sync command running simultaneously."""
    ensure_production_root(root)
    lock = root / '.meteor-ingest-one.lock'
    ensure_destination(lock)
    if lock.is_symlink() or (lock.exists() and not lock.is_file()):
        raise RuntimeError('Unsafe ingest lock path')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another bounded ingest is already running') from exc
        yield


def enqueue_selected(db: sqlite3.Connection, *, device_id: str, segment: dict,
                     config_sha: str) -> int:
    """Queue ONLY this verified segment, not unrelated work from another session."""
    if len(config_sha) != 64 or not re.fullmatch(r'[0-9a-f]{64}', config_sha):
        raise ValueError('Invalid detector config SHA-256')
    segment_id = segment['segment_id']
    digest = segment['sha256'].lower()
    verified = db.execute(
        "SELECT 1 FROM segments WHERE device_id=? AND segment_id=? "
        "AND sha256=? AND state='INGEST_VERIFIED'",
        (device_id, segment_id, digest)).fetchone()
    if verified is None:
        raise RuntimeError('Video not verified; refusing enqueue')
    material = f'DETECT:{device_id}:{segment_id}:{digest}:{METDETPY_VERSION}:{config_sha}'
    unique_key = hashlib.sha256(material.encode()).hexdigest()
    cursor = db.execute("""INSERT OR IGNORE INTO jobs
       (unique_key, kind, device_id, segment_id, video_sha256, detector_version,
        detector_config_sha256, state, created_utc)
       VALUES (?, 'DETECT', ?, ?, ?, ?, ?, 'WAITING', ?)""",
       (unique_key, device_id, segment_id, digest, METDETPY_VERSION, config_sha, utc_now()))
    db.commit()
    return cursor.rowcount


def ingest_one(node: MeteorNode, *, root: Path, device_id: str,
               detector_config: Path, segment_id: str | None = None,
               minimum_bytes: int = MIN_VIDEO_BYTES) -> dict:
    if not DEVICE_ID.fullmatch(device_id):
        raise ValueError('Invalid camera ID')
    if segment_id is not None and not VALID_ID.fullmatch(segment_id):
        raise ValueError('Invalid segment ID')
    if not detector_config.is_file() or detector_config.is_symlink():
        raise ValueError('Missing/unsafe detector config')
    config_sha = sha256_file(detector_config)
    ensure_production_root(root)  # Before any network or writes.
    with exclusive_ingest_lock(root):
        ensure_production_root(root)
        health = node.get_json('/api/v1/health')
        inventory = node.get_json('/api/v1/segments')
        segment = select_segment(inventory, specific=segment_id, min_size=minimum_bytes)
        # Only now create/update the shared production queue; require a real HDD.
        folder = safely_prepare_folder(root, device_id)
        db_path = root / 'meteorhub.sqlite3'
        if db_path.is_symlink():
            raise RuntimeError('Database symlink forbidden')
        ensure_destination(db_path)
        with contextlib.closing(create_database(db_path)) as db:
            from hub_workflow import init_pipeline
            ensure_production_root(root)
            init_pipeline(db)
            result = node.pull_one(segment, folder, min_free_bytes=RESERVE_BYTES)
            video = folder / f"{segment['segment_id']}.mp4"
            sidecar = folder / f"{segment['segment_id']}.json"
            ensure_destination(video)
            ensure_destination(sidecar)
            if sidecar.is_symlink() or not sidecar.is_file():
                raise RuntimeError('Missing verified sidecar')
            MeteorNode.verify_metadata(sidecar, segment['segment_id'],
                                       segment['size_bytes'], segment['sha256'].lower())
            if video.is_symlink() or not video.is_file():
                raise RuntimeError('Missing verified video')
            if sha256_file(video) != segment['sha256'].lower():
                raise RuntimeError('Transferred video hash mismatch')
            state, probe = verify_with_ffprobe(video)
            if state != 'INGEST_VERIFIED':
                # No queue entry for video that is not decodable/valid.
                raise RuntimeError('FFPROBE_REJECTED: ' + state)
            ensure_production_root(root)
            store_verified_segment(db, device_id, segment, state, probe)
            ensure_production_root(root)
            added = enqueue_selected(db, device_id=device_id,
                                     segment=segment, config_sha=config_sha)
            return {
                'status': 'VERIFIED_AND_QUEUED',
                'device_id': device_id,
                'phone_version': health.get('node_version'),
                'segment_id': segment['segment_id'],
                'size_bytes': segment['size_bytes'],
                'sha256': segment['sha256'].lower(),
                'transfer': result,
                'video': str(video),
                'jobs_new_total': added,
                'queue': queue_summary(db),
                'original_phone_files_kept': True,
                'detector_started': False,
                'phone_deleted': False,
            }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='RFC1918 IPv4 of laboratory Node 0.10')
    parser.add_argument('--device-id', default='cam-viva')
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--detector-config', type=Path, required=True)
    parser.add_argument('--segment-id', help='Optional exact READY segment ID; default largest')
    parser.add_argument('--min-mib', type=int, default=50, help='Skip tiny aborted files')
    args = parser.parse_args(argv)
    if not 1 <= args.min_mib <= 8192:
        parser.error('min-mib outside 1..8192')
    token = os.environ.get(TOKEN_ENV, '')
    if len(token) < 32:
        parser.error('Missing lab bearer token environment variable')
    try:
        # No arbitrary ports; HTTP only on private IP and fixed 8080.
        node = MeteorNodeLabHTTP(args.host, 8080, token)
        summary = ingest_one(node, root=args.data_dir, device_id=args.device_id,
                             detector_config=args.detector_config,
                             segment_id=args.segment_id,
                             minimum_bytes=args.min_mib * 1024**2)
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({'status': 'BLOCKED_OR_INCOMPLETE', 'error': str(exc),
                          'phone_deleted': False}, ensure_ascii=False))
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
