#!/usr/bin/python3
"""Purge only phone clips already verified on G62 by MeteorMesh.
Requires per-phone storage admin keys in storage-admin.env; missing keys fail closed.
"""
import hashlib
import http.client
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path('/srv/meteor-data')
UUID = '444a8a6d-090a-456c-8a9d-d99665c32574'
HUB = Path('/home/meteor/.local/share/meteormesh/releases/rc158-20261103-analysis-rounds/MeteorHub')
CONFIG = Path('/home/meteor/.config/meteormesh/devices-three-lab-rc12.json')
RECEIPTS = Path.home() / '.local/share/meteormesh/receipts/phone-purge.jsonl'
CAM = {'cam-surya': 'surya', 'cam-ruby': 'ruby', 'cam-viva': 'viva'}
PAT = re.compile(r'^(surya|ruby|viva)_(\d{8}T\d{6}Z)_[0-9a-f]{12}$')
HEX = re.compile(r'^[0-9a-f]{64}$')
ORDER = ('cam-surya', 'cam-ruby', 'cam-viva')


def log(event, **fields):
    print(json.dumps({'event': event, **fields}, sort_keys=True), flush=True)


def check_disk():
    if ROOT.is_symlink() or not ROOT.is_dir():
        raise RuntimeError('HDD_MISSING_OR_LINK')
    mount = subprocess.run(
        ['findmnt', '-n', '-o', 'TARGET,FSTYPE,UUID', '--mountpoint', str(ROOT)],
        capture_output=True, text=True, check=True, timeout=10)
    if mount.stdout.strip().split() != [str(ROOT), 'ext4', UUID]:
        raise RuntimeError('HDD_UUID_OR_MOUNT_MISMATCH')
    if shutil.disk_usage(ROOT).free < 100 * 1024**3:
        raise RuntimeError('HDD_FREE_BELOW_100_GIB')


def load_receipts():
    deleted = set()
    unresolved = set()
    if RECEIPTS.is_symlink():
        raise RuntimeError('RECEIPT_PATH_IS_SYMLINK')
    if not RECEIPTS.exists():
        return deleted, unresolved
    st = RECEIPTS.stat(follow_symlinks=False)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_mode & 0o077:
        raise RuntimeError('UNSAFE_RECEIPT_FILE')
    for line in RECEIPTS.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        identity = (row.get('device_id'), row.get('segment_id'), row.get('sha256'))
        if row.get('event') == 'attempt':
            unresolved.add(identity)
        elif row.get('event') == 'result':
            status = row.get('status')
            if status == 'UNKNOWN_RECONCILE_NEXT_HOUR':
                unresolved.add(identity)
            else:
                unresolved.discard(identity)
            if status in ('deleted', 'video_deleted_sidecar_left'):
                deleted.add(identity)
    return deleted, unresolved


def receipt_blocks_identity(identity, deleted, unresolved):
    """Keep only this exact segment out after a prior ambiguous purge result."""
    return identity in deleted or identity in unresolved


def append_receipt(row):
    RECEIPTS.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if RECEIPTS.parent.is_symlink():
        raise RuntimeError('RECEIPT_DIRECTORY_IS_SYMLINK')
    fd = os.open(RECEIPTS, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise RuntimeError('UNSAFE_RECEIPT_FILE')
        data = (json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n').encode()
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def host_verified(device_id, segment_id, sha, size, ffprobe_json):
    prefix = CAM.get(device_id)
    match = PAT.fullmatch(segment_id) if isinstance(segment_id, str) else None
    if (prefix is None or match is None or match.group(1) != prefix or
            not isinstance(sha, str) or not HEX.fullmatch(sha) or
            type(size) is not int or size <= 0):
        return False
    folder = ROOT / device_id
    if folder.is_symlink() or not folder.is_dir():
        return False
    path = folder / (segment_id + '.mp4')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size != size or before.st_nlink != 1:
                return False
            digest = hashlib.sha256()
            while block := os.read(fd, 1024 * 1024):
                digest.update(block)
            after = os.fstat(fd)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                return False
            current = os.stat(path, follow_symlinks=False)
            if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                return False
        finally:
            os.close(fd)
        if digest.hexdigest() != sha:
            return False
        recorded = json.loads(ffprobe_json)
        if int(recorded['format']['size']) != size or not any(
                s.get('codec_type') == 'video' and s.get('codec_name') == 'h264'
                for s in recorded['streams']):
            return False
        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries',
             'stream=codec_type,codec_name,width,height:format=size,duration',
             '-of', 'json', str(path)], capture_output=True, text=True, timeout=45)
        if probe.returncode:
            return False
        actual = json.loads(probe.stdout)
        return (int(actual['format']['size']) == size and
                float(actual['format']['duration']) > 1 and
                any(s.get('codec_type') == 'video' and s.get('codec_name') == 'h264'
                    and int(s.get('width', 0)) > 0 and int(s.get('height', 0)) > 0
                    for s in actual['streams']))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return False


def get_client_class():
    sys.path.insert(0, str(HUB))
    from meteornode_pull import MeteorNodeLabHTTP
    return MeteorNodeLabHTTP


def main():
    if os.environ.get('METEORMESH_PHONE_PURGE') != '1':
        log('PURGE_DISABLED')
        return 0

    # Safe by default. A live deletion requires an explicit DRY_RUN=0 override.
    dry_run = os.environ.get('METEORMESH_PHONE_PURGE_DRY_RUN', '1') != '0'
    keys = {}
    for device_id in ORDER:
        suffix = device_id.split('-', 1)[1].upper()
        value = os.environ.get(f'METEOR_NODE_{suffix}_STORAGE_ADMIN_KEY', '')
        if HEX.fullmatch(value):
            keys[device_id] = value

    check_disk()
    scope_path = Path.home() / '.local/share/meteormesh/staging/fullnight/night-scope-hourly-current.json'
    if scope_path.is_symlink() or not scope_path.is_file() or scope_path.stat().st_mode & 0o077:
        raise RuntimeError('HOURLY_SCOPE_MISSING_OR_UNSAFE')
    scope_doc = json.loads(scope_path.read_text(encoding='utf-8'))
    if not isinstance(scope_doc.get('retention_auto_delete', False), bool):
        raise RuntimeError('HOURLY_SCOPE_RETENTION_POLICY_INVALID')
    if (scope_doc.get('status') != 'ARMED' or
            scope_doc.get('automatic_run_allowed') is not True or
            scope_doc.get('ingest_scope_enforced') is not True or
            scope_doc.get('queue_scope_enforced') is not True):
        raise RuntimeError('HOURLY_SCOPE_NOT_APPROVED')
    sys.path.insert(0, str(HUB))
    from night_scope import load_night_scope
    scope = load_night_scope(scope_path)
    if scope.night_id != scope_doc.get('night_id'):
        raise RuntimeError('HOURLY_SCOPE_ID_MISMATCH')

    deleted, unresolved = load_receipts()
    if unresolved:
        log('PURGE_UNRESOLVED_PRIOR_ATTEMPTS_SKIPPED',
            count=len(unresolved),
            devices=sorted({identity[0] for identity in unresolved}),
            scope='exact-segment-only', dry_run=dry_run)

    with sqlite3.connect(f'file:{ROOT}/meteorhub.sqlite3?mode=ro', uri=True, timeout=30) as db:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute(
            "SELECT device_id,segment_id,sha256,size_bytes,ffprobe_json "
            "FROM segments WHERE state='INGEST_VERIFIED'").fetchall()

    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    devices = {row['id']: row for row in config.get('devices', [])
               if row.get('id') in CAM}
    client_class = get_client_class()
    total_deleted = 0
    verified_candidate_count = 0
    unresolved_segment_count = len(unresolved)

    for device_id in ORDER:
        if device_id not in devices:
            log('PURGE_SKIP_CONFIG_MISSING', device_id=device_id)
            continue
        if not dry_run and device_id not in keys:
            log('PURGE_SKIP_STORAGE_KEY_MISSING', device_id=device_id)
            continue
        d = devices[device_id]
        token = os.environ.get(d.get('token_env', ''), '')
        if not token:
            log('PURGE_SKIP_PAIRING_TOKEN_MISSING', device_id=device_id)
            continue
        try:
            client = client_class(d['host'], int(d['port']), token)
            health = client.get_json('/api/v1/health')
            if (health.get('device_id') != CAM[device_id] or
                    health.get('storage_admin_available') is not True):
                log('PURGE_SKIP_PHONE_ID_OR_STORAGE', device_id=device_id)
                continue
            listing = client.get_json('/api/v1/segments')
            remote = {x.get('segment_id'): x for x in listing.get('segments', [])
                      if isinstance(x, dict)}
        except Exception as exc:
            log('PURGE_SKIP_PHONE_UNREACHABLE', device_id=device_id,
                error=type(exc).__name__)
            continue

        candidates = []
        for dev, seg, sha, size, probe in rows:
            identity = (dev, seg, sha)
            if dev != device_id or receipt_blocks_identity(identity, deleted, unresolved):
                continue
            item = remote.get(seg)
            if not item or (item.get('state') is not None and
                            str(item.get('state')).upper() != 'READY'):
                continue
            if item.get('sha256', '').lower() != sha or item.get('size_bytes') != size:
                continue
            candidates.append((seg, sha, size, probe))
        candidates.sort(key=lambda x: x[0])
        if not candidates:
            log('PURGE_NO_VERIFIED_CANDIDATES', device_id=device_id,
                remote_ready=len(remote), dry_run=dry_run)
            continue

        limit = 1 if dry_run else 25
        verified = []
        for seg, sha, size, probe in candidates:
            if host_verified(device_id, seg, sha, size, probe):
                verified.append((seg, sha, size))
                if len(verified) >= limit:
                    break
        if not verified:
            log('PURGE_NO_HOST_VERIFIED_CANDIDATE', device_id=device_id,
                candidate_matches=len(candidates), dry_run=dry_run)
            continue

        verified_candidate_count += len(verified)
        if dry_run:
            log('PURGE_DRY_RUN_CANDIDATES', device_id=device_id,
                verified_batch_count=len(verified),
                first_segment_id=verified[0][0],
                last_segment_id=verified[-1][0],
                matching_candidates=len(candidates))
            continue

        batch = [{'segment_id': seg, 'sha256': sha}
                 for seg, sha, _ in verified]
        for seg, sha, _ in verified:
            append_receipt({'event': 'attempt', 'device_id': device_id,
                            'segment_id': seg, 'sha256': sha})
        conn = None
        try:
            conn = http.client.HTTPConnection(d['host'], int(d['port']), timeout=240)
            body = json.dumps({'segments': batch}, separators=(',', ':')).encode()
            conn.request('POST', '/api/v1/storage/purge-recordings', body=body,
                         headers={
                             'Authorization': f'Bearer {token}',
                             'X-Meteor-Storage-Key': keys[device_id],
                             'Content-Type': 'application/json'})
            response = conn.getresponse()
            payload = response.read(1024 * 1024)
            doc = json.loads(payload)
            results = doc.get('results') if isinstance(doc, dict) else None
            expected_ids = {seg for seg, _, _ in verified}
            result_ids = [item.get('segment_id') for item in results
                          if isinstance(item, dict)] if isinstance(results, list) else []
            if (response.status != 200 or len(result_ids) != len(expected_ids) or
                    set(result_ids) != expected_ids or
                    any(not isinstance(item.get('status'), str)
                        for item in results if isinstance(item, dict))):
                raise RuntimeError('PURGE_RESPONSE_UNVERIFIED')
            result_map = {item['segment_id']: item for item in results}
            batch_deleted = 0
            for seg, sha, size in verified:
                status = result_map[seg]['status']
                append_receipt({'event': 'result', 'device_id': device_id,
                                'segment_id': seg, 'sha256': sha,
                                'size_bytes': size, 'status': status,
                                'http_status': response.status})
                if status in ('deleted', 'video_deleted_sidecar_left'):
                    deleted.add((device_id, seg, sha))
                    total_deleted += 1
                    batch_deleted += 1
                log('PURGE_RESULT', device_id=device_id, segment_id=seg,
                    status=status)
            log('PURGE_DEVICE_BATCH_COMPLETE', device_id=device_id,
                requested_count=len(verified), deleted_count=batch_deleted,
                max_batch_size=25)
        except Exception as exc:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            for seg, sha, size in verified:
                append_receipt({'event': 'result', 'device_id': device_id,
                                'segment_id': seg, 'sha256': sha,
                                'size_bytes': size,
                                'status': 'UNKNOWN_RECONCILE_NEXT_HOUR'})
            unresolved_segment_count += len(verified)
            log('PURGE_RESPONSE_UNKNOWN', device_id=device_id,
                segment_count=len(verified), scope='exact-segments-only',
                error=type(exc).__name__)
            # A transport ambiguity quarantines this exact batch. Other phones
            # continue through the same identity, SHA, ffprobe, and mount gates.
            continue
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


    log('PURGE_CYCLE_COMPLETE', dry_run=dry_run,
        verified_candidate_count=verified_candidate_count,
        deleted_video_count=total_deleted, max_segments_per_device=25,
        unresolved_segment_count=unresolved_segment_count)
    return 0



if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        log('PURGE_FAIL_CLOSED', error=type(exc).__name__, reason=str(exc))
        raise SystemExit(2)
