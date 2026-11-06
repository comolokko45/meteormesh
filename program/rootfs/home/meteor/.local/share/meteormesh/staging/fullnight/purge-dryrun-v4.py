#!/usr/bin/env python3
"""MeteorMesh V4: salt-okunur host kanit denetimi."""

import hashlib
import importlib.util
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path('/srv/meteor-data')
STAGE = Path(__file__).resolve().parent
V3 = STAGE / 'purge-dryrun-v3.py'
V3_SHA = '0e10ee85eb15bff44bcb0225becfda82c77708794dafc45ffa8e98f633ad319a'
SCOPE = STAGE / 'night-scope-current.json'
HUB = Path.home() / '.local/share/meteormesh/releases/rc156-20261101-night-status/MeteorHub'
UUID = '444a8a6d-090a-456c-8a9d-d99665c32574'
CAMERAS = {'cam-surya':'surya', 'cam-ruby':'ruby'}
PAT = re.compile(r'^(surya|ruby)_(\d{8}T\d{6}Z)_[0-9a-f]{12}$')
HEX = re.compile(r'^[a-f0-9]{64}$')
TZ = ZoneInfo('Europe/Istanbul')

def expected_window(now):
    local = now.astimezone(TZ)
    day = local.date() if local.hour >= 19 else local.date()-timedelta(days=1)
    start = datetime.combine(
        day, time(19), TZ
    ).astimezone(timezone.utc)
    end = datetime.combine(
        day+timedelta(days=1), time(5), TZ
    ).astimezone(timezone.utc)
    return f'{day:%Y%m%d}-night', start, end

def valid_segment(dev, seg, sha, start, end):
    if dev not in CAMERAS or not isinstance(seg,str) or not isinstance(sha,str):
        return False
    m = PAT.fullmatch(seg)
    if not m or m.group(1)!=CAMERAS[dev] or not HEX.fullmatch(sha):
        return False
    dt = datetime.strptime(
        m.group(2), '%Y%m%dT%H%M%SZ'
    ).replace(tzinfo=timezone.utc)
    return start <= dt < end

def load_v3():
    if V3.is_symlink() or not V3.is_file():
        raise RuntimeError('V3_MISSING')
    if hashlib.sha256(V3.read_bytes()).hexdigest()!=V3_SHA:
        raise RuntimeError('V3_SHA_MISMATCH')

    spec = importlib.util.spec_from_file_location('mm_purge_v3_verified',V3)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.host_file

def check_disk():
    if not ROOT.is_dir() or ROOT.is_symlink():
        raise RuntimeError('HDD_MISSING')
    p = subprocess.run(
        ['findmnt','-n','-o','TARGET,FSTYPE,UUID',
         '--mountpoint',str(ROOT)],
        capture_output=True,text=True,check=True,timeout=10
    )
    if p.stdout.strip().split()!=[str(ROOT),'ext4',UUID]:
        raise RuntimeError('HDD_UUID_MISMATCH')
    dbfile=ROOT/'meteorhub.sqlite3'
    if dbfile.is_symlink() or not dbfile.is_file():
        raise RuntimeError('DATABASE_MISSING_OR_LINK')

def audit():
    report = {
        'mode':'HOST_EVIDENCE_DRY_RUN_V4',
        'delete_authorized':False,
        'phone_delete':False,
        'hdd_delete':False,
        'phone_receipt':'NOT_IMPLEMENTED',
        'full_night_accepted':False
    }

    night,start,end = expected_window(datetime.now(timezone.utc))
    report['expected_night']=night
    check_disk()

    if SCOPE.is_symlink() or not SCOPE.is_file():
        raise RuntimeError('SCOPE_MISSING_OR_LINK')
    if SCOPE.stat().st_mode & 0o077:
        raise RuntimeError('SCOPE_UNSAFE_PERMISSIONS')

    sys.path.insert(0,str(HUB))
    from night_scope import load_night_scope
    scope=load_night_scope(SCOPE)

    report['active_night']=scope.night_id
    if scope.night_id!=night:
        report['status']='WAIT_FOR_SCOPE_ROTATION'
        return report

    config=json.loads(SCOPE.read_text())
    if (
        config.get('status')!='ARMED'
        or config.get('start_utc_ms')!=int(start.timestamp()*1000)
        or config.get('end_utc_ms')!=int(end.timestamp()*1000)
        or config.get('automatic_run_allowed') is not True
        or config.get('ingest_scope_enforced') is not True
        or config.get('queue_scope_enforced') is not True
        or config.get('retention_auto_delete') is not False
    ):
        raise RuntimeError('SCOPE_METADATA_INVALID')

    with sqlite3.connect(
        f'file:{ROOT}/meteorhub.sqlite3?mode=ro',uri=True
    ) as db:
        db.execute('PRAGMA query_only=ON')
        rows=db.execute("""
            SELECT device_id,segment_id,sha256,
                   size_bytes,ffprobe_json
            FROM segments
            WHERE state='INGEST_VERIFIED'
        """).fetchall()

    matches=[
        r for r in rows
        if valid_segment(r[0],r[1],r[2],start,end)
        and scope.allows(r[0],r[1],r[2])
    ]

    report['current_night_verified_rows']=len(matches)
    report['cameras_with_verified_segments']=sorted(
        {r[0] for r in matches}
    )

    if not matches:
        report['status']='NO_CURRENT_NIGHT_VERIFIED_SEGMENT'
        return report

    sample=min(
        matches,
        key=lambda r:r[3] if isinstance(r[3],int) else float('inf')
    )
    dev,seg,sha,size,metadata=sample
    result=load_v3()(dev,seg,sha,size,metadata)

    report['sample']={
        'camera':dev,
        'segment':seg,
        'size_bytes':size,
        'host_result':result,
        'remote_segment_state':'NOT_CHECKED',
        'remote_acknowledgement':'NOT_AVAILABLE',
        'delete_authorized':False
    }

    report['other_segments_not_file_checked']=len(matches)-1
    report['status']=(
        'HOST_SAMPLE_VERIFIED'
        if result=='HOST_VERIFIED_NOT_DELETE_AUTHORIZED'
        else 'HOST_SAMPLE_REJECTED'
    )
    return report

def self_test():
    utc=timezone.utc
    night,start,end=expected_window(
        datetime(2026,11,1,21,tzinfo=utc)
    )
    assert night=='20261101-night'
    assert start==datetime(2026,11,1,16,tzinfo=utc)
    assert end==datetime(2026,11,2,2,tzinfo=utc)

    seg='surya_20261101T200000Z_012345abcdef'
    sha='a'*64

    assert valid_segment('cam-surya',seg,sha,start,end)
    assert not valid_segment('cam-ruby',seg,sha,start,end)
    assert not valid_segment('cam-surya','../'+seg,sha,start,end)
    assert not valid_segment('cam-surya',seg,'bad',start,end)
    assert not valid_segment(
        'cam-surya','surya_20261101T020000Z_012345abcdef',
        sha,start,end
    )
    assert not valid_segment(
        'cam-surya','surya_20261102T050000Z_012345abcdef',
        sha,start,end
    )
    assert expected_window(
        datetime(2026,11,2,3,tzinfo=utc)
    )[0]=='20261101-night'

    print('V4_GECE_KIMLIK_TESTI: 9/9 PASS')

if __name__=='__main__':
    if sys.argv[1:]==['--self-test']:
        self_test()
    elif sys.argv[1:]:
        raise SystemExit('USAGE: purge-dryrun-v4.py [--self-test]')
    else:
        try:
            print(json.dumps(
                audit(),ensure_ascii=False,sort_keys=True,indent=2
            ))
        except Exception as exc:
            print(json.dumps({
                'status':'FAIL_CLOSED',
                'reason':f'{type(exc).__name__}: {exc}',
                'delete_authorized':False,
                'phone_delete':False,
                'hdd_delete':False
            },ensure_ascii=False,indent=2))
            sys.exit(2)
