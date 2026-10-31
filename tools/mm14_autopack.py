#!/usr/bin/env python3
"""Package *existing* verified test previews; never create new clips.

No camera, recording, SQLite, original video, timer, network or deletion actions.
Runs after event-review on existing RC1.4 analysis service. Existing test clips
are opt-in: only a reviewed JSON receipt accompanied by its MP4 is eligible.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

DATA = Path('/srv/meteor-data')
HUB = Path.home() / '.local/share/meteormesh/releases/rc14-20261031-split/MeteorHub'
BUNDLER = Path.home() / '.local/lib/meteormesh/meteor-bundle-once.py'
DAY = re.compile(r'20\d{6}\Z')
EVENT = re.compile(r'e_[a-f0-9]{24}\Z')


def discover(root: Path, max_scan: int = 5000):
    """Inspect only concrete non-symlinked test receipts on the verified HDD."""
    if root.is_symlink():
        raise RuntimeError('UNSAFE_CLIP_ROOT')
    if not root.is_dir():
        return []
    found = []
    seen = 0
    for folder in sorted(root.iterdir(), key=lambda x: x.name):
        if folder.is_symlink():
            raise RuntimeError('SYMLINK_NIGHT_DIRECTORY')
        if not DAY.fullmatch(folder.name):
            continue
        if not folder.is_dir():
            raise RuntimeError('INVALID_NIGHT_DIRECTORY')
        for file in sorted(folder.iterdir(), key=lambda x: x.name):
            if file.is_symlink():
                raise RuntimeError('SYMLINK_CLIP_RECEIPT')
            if file.suffix != '.json':
                continue
            if not file.is_file() or not EVENT.fullmatch(file.stem):
                raise RuntimeError('UNEXPECTED_CLIP_RECEIPT_NAME')
            seen += 1
            if seen > max_scan:
                raise RuntimeError('TOO_MANY_CLIP_RECEIPTS')
            found.append((folder.name, file.stem))
    return found


def load_bundler():
    if BUNDLER.is_symlink() or not BUNDLER.is_file():
        raise RuntimeError('PINNED_BUNDLER_NOT_INSTALLED')
    spec = importlib.util.spec_from_file_location('mm14_bundle_once', BUNDLER)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def run(limit: int) -> int:
    if not 1 <= limit <= 10:
        raise ValueError('INVALID_PER_RUN_LIMIT')
    if HUB.is_symlink() or not (HUB / 'storage_guard.py').is_file():
        raise RuntimeError('APPROVED_HUB_RELEASE_MISSING')
    sys.path.insert(0, str(HUB))
    from storage_guard import GuardedStorage, StoragePolicy
    # Fail closed before examining /srv/meteor-data. Never write to an unmounted folder.
    with GuardedStorage(StoragePolicy()) as guard:
        guard.verify()
        items = discover(DATA / 'event-clips')
    bundler = load_bundler()
    created, existing, invalid, errors = 0, 0, 0, []
    for day, eid in items:
        dst = DATA / 'event-packages' / day / (eid + '.json')
        if dst.is_symlink():
            errors.append(f'{day}/{eid}:SYMLINK_PACKAGE')
            continue
        if dst.is_file():
            existing += 1
            continue
        if created >= limit:
            continue
        try:
            # Rechecks HDD UUID + clip/receipt/index SHA-256 in guarded bundler.
            bundler.run(day, eid)
            created += 1
        except Exception as exc:
            invalid += 1
            errors.append(f'{day}/{eid}:{type(exc).__name__}:{str(exc)[:70]}')
    report = {'created': created, 'existing': existing, 'invalid': invalid,
              'eligible_receipts': len(items), 'limit': limit,
              'errors': errors[:5], 'test_only': True,
              'clips_generated': 0, 'originals_deleted': False, 'upload': False}
    print('EVENT_AUTO_PACKAGE: ' + json.dumps(report, sort_keys=True), flush=True)
    return 0  # Observer: do not override analysis service's exit status.


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--limit', type=int, default=10)
    args = p.parse_args()
    try:
        return run(args.limit)
    except Exception as exc:
        print('EVENT_AUTO_PACKAGE_UNAVAILABLE: ' + type(exc).__name__ + ': ' + str(exc)[:160], flush=True)
        return 0  # Journal warning, never masks the detector's own result.


if __name__ == '__main__':
    raise SystemExit(main())
