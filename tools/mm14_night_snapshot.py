#!/usr/bin/env python3
"""MeteorMesh night analysis journal snapshot (read-only, provisional, no archive).

No writes to HDD/SQLite. Reads only VERIFIED segment identities and SUCCEEDED
MetDetPy results; confirms detector-result SHA-256, never asserts meteor certainty.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path

EXPECTED_UUID = '444a8a6d-090a-456c-8a9d-d99665c32574'
DEFAULT_ROOT = Path('/srv/meteor-data')
DEFAULT_SCOPE = Path.home()/'.local/share/meteormesh/staging/fullnight/night-scope-current.json'
NODES = ('cam-viva', 'cam-ruby', 'cam-surya')
NAME = re.compile(r'^[a-zA-Z0-9_-]+_(\d{8}T\d{6}Z)_[0-9a-f]{12}$')
HEX = re.compile(r'^[0-9a-f]{64}$')


def safe_scope(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or (path.stat().st_mode & 0o077):
        raise RuntimeError('NIGHT_SCOPE_NOT_SECURE')
    obj = json.loads(path.read_text(encoding='utf-8'))
    if obj.get('schema') != 'meteormesh.night_scope.v1' or obj.get('status') != 'ARMED':
        raise RuntimeError('NIGHT_SCOPE_NOT_ARMED')
    a,b=obj.get('start_utc_ms'),obj.get('end_utc_ms')
    if type(a) is not int or type(b) is not int or not 1700000000000<a<b<=a+16*3600*1000:
        raise RuntimeError('NIGHT_SCOPE_BAD_WINDOW')
    if not isinstance(obj.get('night_id'),str) or not re.fullmatch('[A-Za-z0-9_-]{5,60}',obj['night_id']):
        raise RuntimeError('NIGHT_SCOPE_BAD_ID')
    if obj.get('automatic_run_allowed') is not True or obj.get('ingest_scope_enforced') is not True:
        raise RuntimeError('NIGHT_SCOPE_NOT_APPROVED')
    excluded=obj.get('excluded_segments')
    if not isinstance(excluded,dict) or set(excluded)!=set(NODES):
        raise RuntimeError('NIGHT_SCOPE_MISSING_BASELINE')
    return obj


def is_current(scope: dict, device: str, segment: str, digest: str) -> bool:
    if device not in NODES or not isinstance(digest,str) or not HEX.fullmatch(digest):
        return False
    m=NAME.fullmatch(segment) if isinstance(segment,str) else None
    if m is None: return False
    stamp=dt.datetime.strptime(m.group(1),'%Y%m%dT%H%M%SZ').replace(tzinfo=dt.timezone.utc)
    tick=int(stamp.timestamp()*1000)
    if not scope['start_utc_ms']<=tick<scope['end_utc_ms']:
        return False
    exclusions=scope['excluded_segments'][device]
    if segment in exclusions:
        if exclusions[segment]!=digest:
            raise RuntimeError('SEGMENT_BASELINE_DIGEST_CHANGED')
        return False
    return True


def count_candidates(path: Path, expected_sha: str, output_dir: Path) -> int:
    if not isinstance(expected_sha,str) or not HEX.fullmatch(expected_sha):
        raise RuntimeError('INVALID_DETECTOR_DIGEST')
    resolved=path.resolve(strict=True)
    expected_root=output_dir.resolve(strict=True)
    if path.is_symlink() or not resolved.is_relative_to(expected_root) or not resolved.is_file():
        raise RuntimeError('DETECTOR_RESULT_OUTSIDE_DATA_ROOT')
    if resolved.stat().st_size>20*1024*1024:
        raise RuntimeError('DETECTOR_RESULT_EXCEEDS_20MB')
    content=resolved.read_bytes()
    if hashlib.sha256(content).hexdigest()!=expected_sha:
        raise RuntimeError('DETECTOR_RESULT_HASH_MISMATCH')
    document=json.loads(content)
    records=document.get('results') if isinstance(document,dict) else None
    if not isinstance(records,list):
        raise RuntimeError('INVALID_DETECTOR_RESULT_SCHEMA')
    return sum(1 for x in records if isinstance(x,dict) and isinstance(x.get('target'),list)
               for t in x['target'] if isinstance(t,dict))


def snapshot(db: sqlite3.Connection, scope: dict, data_root: Path) -> dict:
    counts={d:{'verified_segments':0,'jobs':{'WAITING':0,'RUNNING':0,'SUCCEEDED':0,'FAILED':0},
               'detector_candidates_unreviewed':0,'result_integrity_errors':0} for d in NODES}
    for dev,seg,digest,state in db.execute('SELECT device_id,segment_id,sha256,state FROM segments'):
        if state=='INGEST_VERIFIED' and is_current(scope,dev,seg,digest):
            counts[dev]['verified_segments']+=1
    results=[]
    for row in db.execute('''SELECT j.id,j.device_id,j.segment_id,j.video_sha256,j.state,
                                r.result_path,r.result_sha256
                             FROM jobs j LEFT JOIN detector_runs r ON r.job_id=j.id
                             ORDER BY j.id'''):
        jid,dev,seg,digest,state,path,sha=row
        if not is_current(scope,dev,seg,digest): continue
        if state not in counts[dev]['jobs']:
            raise RuntimeError('UNKNOWN_JOB_STATE')
        counts[dev]['jobs'][state]+=1
        if state=='SUCCEEDED':
            if not isinstance(path,str) or not isinstance(sha,str):
                counts[dev]['result_integrity_errors']+=1
            else:
                results.append((jid,dev,seg,Path(path),sha))
    errors=[]
    for jid,dev,seg,path,sha in results:
        try:
            counts[dev]['detector_candidates_unreviewed']+=count_candidates(path,sha,data_root/'detector-results')
        except (RuntimeError,OSError,ValueError,json.JSONDecodeError) as exc:
            counts[dev]['result_integrity_errors']+=1
            errors.append({'job_id':jid,'device':dev,'reason':type(exc).__name__+': '+str(exc)[:140]})
    pending=sum(x['jobs']['WAITING']+x['jobs']['RUNNING']+x['jobs']['FAILED'] for x in counts.values())
    missing=[dev for dev,x in counts.items() if not x['verified_segments']]
    integrity=sum(x['result_integrity_errors'] for x in counts.values())
    return {'schema':'meteormesh.night-analysis-snapshot.v1',
            'night_id':scope['night_id'],'status':('REVIEW_REQUIRED' if integrity else
                'PARTIAL' if missing or pending else 'PROVISIONAL_ANALYZED'),
            'missing_verified_devices':missing,'jobs_not_successful':pending,
            'detector_candidates_unreviewed':sum(x['detector_candidates_unreviewed'] for x in counts.values()),
            'devices':counts,'errors':errors[:20],
            'claims':{'confirmed_meteors':False,'all_camera_coverage_proven':False,
                      'original_video_rehashed':False,'archive_verified':False,'auto_delete':False}}


def verify_mount(root: Path) -> None:
    if root!=DEFAULT_ROOT or root.is_symlink():
        raise RuntimeError('UNAPPROVED_DATA_ROOT')
    r=subprocess.run(['findmnt','-n','-o','UUID,FSTYPE','--mountpoint',str(root)],
                     capture_output=True,text=True,timeout=8,check=False)
    if r.returncode or r.stdout.split()!=[EXPECTED_UUID,'ext4']:
        raise RuntimeError('MOUNT_UUID_NOT_VERIFIED')


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--data-root',type=Path,default=DEFAULT_ROOT)
    ap.add_argument('--scope',type=Path,default=DEFAULT_SCOPE)
    args=ap.parse_args()
    try:
        verify_mount(args.data_root)
        scope=safe_scope(args.scope)
        dbfile=args.data_root/'meteorhub.sqlite3'
        if dbfile.is_symlink() or not dbfile.is_file():
            raise RuntimeError('DB_FILE_MISSING')
        with sqlite3.connect(dbfile.as_uri()+'?mode=ro',uri=True,timeout=5) as db:
            db.execute('PRAGMA query_only=ON')
            result=snapshot(db,scope,args.data_root)
        print('NIGHT_ANALYSIS_SNAPSHOT: '+json.dumps(result,ensure_ascii=False,sort_keys=True))
    except (RuntimeError,OSError,ValueError,sqlite3.Error,subprocess.TimeoutExpired,json.JSONDecodeError) as exc:
        print('NIGHT_ANALYSIS_SNAPSHOT_UNAVAILABLE: '+type(exc).__name__+': '+str(exc)[:200])
    return 0   # This is a journal-only observer; never masks actual analysis exit status.

if __name__=='__main__':
    raise SystemExit(main())