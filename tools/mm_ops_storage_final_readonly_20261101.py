#!/usr/bin/env python3
"""MeteorMesh RC1.5 host storage acceptance - READ ONLY.

Measures verified HDD capacity, checks the newest independent SSD SQLite backup
against its RC153 receipt and SHA256, and estimates hypothetical night headroom.
Never opens production SQLite, scans MP4 content, creates backups, deletes data,
calls SMART, changes services, accesses phones, or sends external notifications.
The backup's quick_check is the *creation-time receipt claim*, NOT a fresh
database restore test. A receipt/hash match alone cannot protect raw videos.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT=Path('/srv/meteor-data')
EXPECTED='444a8a6d-090a-456c-8a9d-d99665c32574'
BACKUPS=Path.home()/'.local/share/meteormesh/db-backups'
RESERVE_GIB=100.0
EARLY_GIB=150.0
MAX_BACKUP_HASH_BYTES=256*1024*1024
MAX_RECEIPT_BYTES=8192
# 20261031-night: 119 Ruby VERIFIED segments total, not full 10 hours.
# A scenario from one historical camera, NOT verified for Viva/Surya.
RUBY_SAMPLE_BYTES=15011734785
RUBY_SAMPLE_SECONDS=17413.101
SCENARIO_PHONES=3
SCENARIO_HOURS=10
NAME_RE=re.compile(r'^meteorhub-(\d{8}T\d{6}Z)-([0-9a-f]{8})\.sqlite3$')

def mount():
    if ROOT.is_symlink() or not ROOT.is_dir() or not os.path.ismount(ROOT):
        return {'verified':False,'reason':'MOUNT_MISSING_OR_SYMLINK'}
    try:
        proc=subprocess.run(['findmnt','-n','-o','UUID,FSTYPE','--mountpoint',
                             str(ROOT)],capture_output=True,text=True,timeout=6)
        if proc.returncode or proc.stdout.split()!=[EXPECTED,'ext4']:
            return {'verified':False,'reason':'UUID_OR_FILESYSTEM_MISMATCH'}
        fs=os.statvfs(ROOT)
        free=fs.f_bavail*fs.f_frsize/1024**3
        return {'verified':True,'uuid_matches':True,'fstype':'ext4',
                'free_gib':round(free,2),
                'data_device':ROOT.stat().st_dev}
    except (OSError,subprocess.TimeoutExpired):
        return {'verified':False,'reason':'MOUNT_READ_ERROR'}

def capacity(free_gib):
    scenario=RUBY_SAMPLE_BYTES/RUBY_SAMPLE_SECONDS*3600*SCENARIO_HOURS*SCENARIO_PHONES/1024**3
    reserve_margin=max(0.0,free_gib-RESERVE_GIB) if free_gib is not None else None
    band=('UNKNOWN' if free_gib is None else 'BELOW_OPERATIONAL_RESERVE'
          if free_gib<RESERVE_GIB else 'EARLY_WARNING'
          if free_gib<EARLY_GIB else 'CAPACITY_CURRENTLY_ABOVE_ALERT_THRESHOLD')
    return {
        'band':band,'free_gib':round(free_gib,2) if free_gib is not None else None,
        'early_warning_gib':EARLY_GIB,'operational_reserve_gib':RESERVE_GIB,
        'reserve_enforced_by_ingest':'NOT_VERIFIED',
        'scenario_source':'20261031 RUBY 15,011,734,785 bytes / 17,413.101s',
        'scenario_limit':'EXTRAPOLATION_ONLY__NOT_A_THREE_PHONE_MEASUREMENT',
        'scenario_three_phones_ten_hours_gib':round(scenario,2),
        'estimated_nights_until_reserve':round(reserve_margin/scenario,2) if reserve_margin is not None else None,
        'whole_nights_before_reserve_scenario':math.floor(reserve_margin/scenario) if reserve_margin is not None else None,
        'other_data_growth_included':False,
    }

def safe_file(p:Path, limit:int):
    try:
        s=p.lstat()
        if (not stat.S_ISREG(s.st_mode) or s.st_size<=0 or s.st_size>limit
                or s.st_uid!=os.getuid() or s.st_mode & 0o077):
            return False
        return True
    except OSError:
        return False

def hash_regular(p:Path,limit:int):
    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_CLOEXEC',0)
    fd=os.open(p,flags)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size<=0 or st.st_size>limit:
            raise ValueError('BACKUP_SIZE_OR_TYPE_UNSAFE')
        if st.st_uid!=os.getuid() or st.st_mode&0o077:
            raise ValueError('BACKUP_PERMISSIONS_UNSAFE')
        sha=hashlib.sha256()
        total=0
        with os.fdopen(fd,'rb',closefd=False) as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):
                total+=len(block)
                if total>limit:raise ValueError('BACKUP_LIMIT_EXCEEDED')
                sha.update(block)
        if total!=st.st_size:raise ValueError('BACKUP_CHANGED_DURING_READ')
        return sha.hexdigest(),total
    finally:
        os.close(fd)

def verify_backup(hdd_device):
    try:
        s=BACKUPS.lstat()
    except FileNotFoundError:
        return {'state':'BACKUP_FOLDER_MISSING'}
    except OSError:
        return {'state':'BACKUP_FOLDER_INACCESSIBLE'}
    if (not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.getuid()
            or s.st_mode&0o077):
        return {'state':'BACKUP_FOLDER_UNSAFE'}
    if hdd_device is not None and s.st_dev==hdd_device:
        return {'state':'NOT_AN_INDEPENDENT_SSD_DEVICE'}
    try:
        entries=[p for p in BACKUPS.glob('meteorhub-*.sqlite3')
                 if NAME_RE.fullmatch(p.name)]
        if not entries:return {'state':'NO_BACKUP_FOUND'}
        # UTC timestamp prefix sorts lexically; no older-file fallback.
        latest=max(entries,key=lambda p:p.name)
        mat=NAME_RE.fullmatch(latest.name)
        assert mat is not None
        receipt=latest.with_suffix('.json')
        if not safe_file(latest,MAX_BACKUP_HASH_BYTES):
            return {'state':'NEWEST_BACKUP_FILE_NOT_SAFE','backup':latest.name}
        if not safe_file(receipt,MAX_RECEIPT_BYTES):
            return {'state':'LATEST_RECEIPT_MISSING_OR_UNSAFE','backup':latest.name}
        with receipt.open('r',encoding='utf-8') as stream:
            doc=json.load(stream)
        if not isinstance(doc,dict):
            return {'state':'RECEIPT_SCHEMA_INVALID','backup':latest.name}
        expected_keys={
          'schema':'meteormesh.db-backup.v1','backup_file':latest.name,
          'source_uuid':EXPECTED,'source':'/srv/meteor-data/meteorhub.sqlite3',
          'quick_check':'ok','raw_videos_copied':False,
          'originals_deleted':False,'ssd_storage_independent':True,
          'created_utc':mat.group(1),
        }
        if any(doc.get(k)!=v for k,v in expected_keys.items()):
            return {'state':'RECEIPT_FIELDS_MISMATCH','backup':latest.name}
        digest=doc.get('sha256','')
        count=doc.get('bytes')
        if not isinstance(digest,str) or re.fullmatch('[0-9a-f]{64}',digest) is None:
            return {'state':'RECEIPT_SHA_INVALID','backup':latest.name}
        if isinstance(count,bool) or not isinstance(count,int) or count<=0:
            return {'state':'RECEIPT_SIZE_INVALID','backup':latest.name}
        actual_sha,actual_bytes=hash_regular(latest,MAX_BACKUP_HASH_BYTES)
        if actual_bytes!=count or actual_sha!=digest:
            return {'state':'LATEST_RECEIPT_HASH_MISMATCH','backup':latest.name}
        when=dt.datetime.strptime(mat.group(1),'%Y%m%dT%H%M%SZ').replace(
            tzinfo=dt.timezone.utc)
        age=(dt.datetime.now(dt.timezone.utc)-when).total_seconds()/3600
        if age < -0.1:
            return {'state':'BACKUP_TIMESTAMP_IN_FUTURE','backup':latest.name}
        return {'state':'LATEST_RECEIPT_SHA_MATCH',
            'backup':latest.name,'age_hours':round(age,2),
            'backup_bytes':actual_bytes,
            'receipt_creation_quick_check':'ok',
            'fresh_quick_check_performed':False,
            'production_sqlite_opened':False,
            'backup_is_database_only':True,
            'raw_video_backup_verified':False,
            'independent_disk':hdd_device is not None}
    except (OSError,ValueError,TypeError,json.JSONDecodeError):
        return {'state':'BACKUP_READ_OR_RECEIPT_ERROR'}

def selftest():
    c=capacity(376.76)
    assert c['band']=='CAPACITY_CURRENTLY_ABOVE_ALERT_THRESHOLD'
    assert 86<c['scenario_three_phones_ten_hours_gib']<88
    assert 3.1<c['estimated_nights_until_reserve']<3.3
    assert capacity(149.9)['band']=='EARLY_WARNING'
    assert capacity(99.9)['band']=='BELOW_OPERATIONAL_RESERVE'
    assert capacity(None)['estimated_nights_until_reserve'] is None
    assert NAME_RE.fullmatch('meteorhub-20261101T070441Z-ffaff6e7.sqlite3')
    assert not NAME_RE.fullmatch('meteorhub-../../foo.sqlite3')
    print('STORAGE_ACCEPTANCE_SELFTEST: PASS')
    print('NO_EXISTING_MM01_OR_MP4_TESTS_REPEATED')
    print('NO_LOCAL_STATE_MODIFIED')

def main():
    parser=argparse.ArgumentParser()
    x=parser.add_mutually_exclusive_group(required=True)
    x.add_argument('--self-test',action='store_true')
    x.add_argument('--preview',action='store_true')
    args=parser.parse_args()
    if args.self_test:
        selftest();return
    hdd=mount()
    b=verify_backup(hdd.get('data_device'))
    hdd.pop('data_device',None)
    c=capacity(hdd.get('free_gib') if hdd['verified'] else None)
    findings=[]
    if not hdd['verified']:findings.append('BLOCKED_HDD_MOUNT_UNVERIFIED')
    if c['band']=='EARLY_WARNING':findings.append('CAPACITY_EARLY_WARNING')
    if c['band']=='BELOW_OPERATIONAL_RESERVE':findings.append('HDD_BELOW_100_GIB_OPERATIONAL_RESERVE')
    if b['state']!='LATEST_RECEIPT_SHA_MATCH':findings.append('SSD_DB_BACKUP_RECEIPT_NOT_VERIFIED')
    elif b.get('age_hours',0)>36:findings.append('SSD_DB_BACKUP_OLDER_THAN_36H')
    findings += ['RAW_MP4_OFF_DISK_BACKUP_NOT_VERIFIED',
                 'HDD_446_REALLOCATED_SECTORS_HISTORICAL__NOT_RETESTED']
    print(json.dumps({
       'schema':'meteormesh.storage-final-readonly.v1',
       'timestamp_utc':dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
       'hdd':hdd,'capacity':c,'sqlite_ssd_backup':b,'findings':findings,
       'read_only':True,'writes_performed':0,'smart_test_performed':False,
       'sqlite_production_database_opened':False,'raw_mp4_read':False,
       'deletion_or_archive_performed':False,'cloud_upload_performed':False,
       'services_or_timers_changed':False,
    },indent=2,ensure_ascii=False))
if __name__=='__main__':
    try:main()
    except (OSError,ValueError,subprocess.TimeoutExpired) as ex:
        print('STORAGE_ACCEPTANCE_ERROR:',type(ex).__name__,str(ex)[:140],
              file=sys.stderr)
        sys.exit(2)
