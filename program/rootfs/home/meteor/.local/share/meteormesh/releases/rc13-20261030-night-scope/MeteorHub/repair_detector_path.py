#!/usr/bin/env python3
"""Repair ONE stale detector result path after confirming its original SHA-256.

No MP4 reads, detector runs, files moved/deleted, or schema edits.
The only optional DB mutation is detector_runs.result_path for exactly one
completed job; failure leaves the row unchanged. Live root is fixed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
from pathlib import Path

from meteornode_pull import VALID_ID
from storage_guard import GuardedStorage, StorageDenied

ROOT = Path('/srv/meteor-data')
SHA = re.compile(r'[0-9a-f]{64}\Z')


def _record(db, device: str, segment: str):
    if not VALID_ID.fullmatch(device) or not VALID_ID.fullmatch(segment):
        raise RuntimeError('INVALID_IDENTIFIERS')
    rows = db.execute('''SELECT r.job_id, r.result_path, r.result_sha256,
                       r.config_sha256, j.state
                       FROM detector_runs r JOIN jobs j ON j.id=r.job_id
                       WHERE r.device_id=? AND r.segment_id=?''',
                      (device, segment)).fetchall()
    if len(rows) != 1:
        raise RuntimeError(f'EXPECTED_EXACTLY_ONE_DETECTOR_RUN_FOUND_{len(rows)}')
    job_id, old, digest, config_sha, state = rows[0]
    if state != 'SUCCEEDED' or not SHA.fullmatch(digest) or not SHA.fullmatch(config_sha):
        raise RuntimeError('INVALID_DETECTOR_RUN_RECORD')
    leaf = f'{segment}_{config_sha[:10]}.json'
    expected = ROOT / 'detector-results' / device / leaf
    if old != str(expected) and not re.fullmatch(
            r'/proc/self/fd/[0-9]+/' + re.escape(leaf), old):
        raise RuntimeError('UNEXPECTED_OLD_PATH_REFUSE_REPAIR')
    return (job_id, old, digest, str(expected), leaf)


def _checked_result_file(guard, device: str, leaf: str, digest: str) -> int:
    """Read the result through verified dirfd + O_NOFOLLOW, not its pathname."""
    guard.verify()
    parent = guard._open_parent(['detector-results', device], create=False)
    fd = None
    try:
        fd = os.open(leaf, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=parent)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_dev != guard._initial_dev:
            raise RuntimeError('RESULT_NOT_REGULAR_ON_VERIFIED_HDD')
        if not 0 < st.st_size <= 128 * 1024 * 1024:
            raise RuntimeError('RESULT_JSON_SIZE_OUT_OF_BOUNDS')
        with os.fdopen(fd, 'rb') as src:
            fd = None
            h = hashlib.sha256()
            while chunk := src.read(1024 * 1024):
                guard.verify()
                h.update(chunk)
            if h.hexdigest() != digest:
                raise RuntimeError('RESULT_HASH_MISMATCH_NO_UPDATE')
            src.seek(0)
            data = json.load(src)
            if not isinstance(data, dict) or not isinstance(data.get('results'), list):
                raise RuntimeError('RESULT_JSON_SCHEMA_INVALID')
        st2 = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        if (st.st_dev, st.st_ino, st.st_size) != (st2.st_dev, st2.st_ino, st2.st_size):
            raise RuntimeError('RESULT_CHANGED_DURING_VERIFICATION')
        guard.verify()
        return len(data['results'])
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def repair_one(db: sqlite3.Connection, guard, device: str, segment: str,
               *, apply: bool = False) -> dict:
    job_id, old, digest, expected, leaf = _record(db, device, segment)
    n = _checked_result_file(guard, device, leaf, digest)
    already = old == expected
    if apply and not already:
        guard.verify()
        db.execute('BEGIN IMMEDIATE')
        try:
            row = _record(db, device, segment)
            if row != (job_id, old, digest, expected, leaf):
                raise RuntimeError('ROW_CHANGED_DURING_REPAIR')
            change = db.execute('''UPDATE detector_runs SET result_path=?
                                  WHERE job_id=? AND result_path=? AND result_sha256=?''',
                                (expected, job_id, old, digest))
            if change.rowcount != 1:
                raise RuntimeError('CONCURRENT_UPDATE_REFUSED')
            guard.verify()
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return {'job_id': job_id, 'segment_id': segment, 'validated_results': n,
            'hash_match': True, 'old_path': old, 'stable_path': expected,
            'status': ('ALREADY_CORRECT' if already else
                       'REPAIRED' if apply else 'REPAIR_AVAILABLE'),
            'modified_rows': 1 if apply and not already else 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True)
    parser.add_argument('--segment', required=True)
    parser.add_argument('--apply', action='store_true', help='Commit only a verified one-row path correction')
    args = parser.parse_args()
    # Production root/UUID/fstype are defined solely by StoragePolicy defaults.
    with GuardedStorage() as guard:
        db = guard.connect_sqlite('meteorhub.sqlite3', read_only=not args.apply)
        result = repair_one(db, guard, args.device, args.segment, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, sqlite3.Error, OSError, StorageDenied) as exc:
        print('RESULT_REPAIR_BLOCKED:', type(exc).__name__, str(exc))
        raise SystemExit(2)
