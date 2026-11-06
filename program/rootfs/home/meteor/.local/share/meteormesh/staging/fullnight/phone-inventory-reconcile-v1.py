#!/usr/bin/env python3
"""MeteorMesh: telefon envanteri / host SQLite salt-okunur eslestirme."""
import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT=Path('/srv/meteor-data')
HUB=Path.home()/'.local/share/meteormesh/releases/rc156-20261101-night-status/MeteorHub'
CONFIG=Path.home()/'.config/meteormesh/devices-two-ops.json'
SCOPE=Path.home()/'.local/share/meteormesh/staging/fullnight/night-scope-current.json'
UUID='444a8a6d-090a-456c-8a9d-d99665c32574'
PREFIX={'cam-surya':'surya','cam-ruby':'ruby'}
SHA=re.compile(r'^[0-9a-f]{64}$')
ENVNAME=re.compile(r'^[A-Z][A-Z0-9_]{2,100}$')

def token_for(name):
    if not isinstance(name,str) or not ENVNAME.fullmatch(name):
        return None
    if os.environ.get(name):
        return os.environ[name]

    # Yalnizca mevcut systemd EnvironmentFile dosyalarini okur.
    # Dosya yolu veya token degeri rapora yazilmaz.
    for unit in ('meteormesh-midnight.service','meteormesh-morning.service'):
        proc=subprocess.run(
            ['systemctl','--user','show',unit,
             '-p','EnvironmentFiles','--value'],
            capture_output=True,text=True,timeout=10
        )
        if proc.returncode:
            continue
        paths=re.findall(
            r'(/[^\s()]+)\s+\(ignore_errors=(?:yes|no)\)',
            proc.stdout
        )
        for filename in paths:
            path=Path(filename)
            try:
                st=path.lstat()
                if path.is_symlink() or not path.is_file():
                    continue
                if st.st_uid not in (0,os.getuid()):
                    continue
                for line in path.read_text().splitlines():
                    line=line.strip()
                    if line.startswith('export '):
                        line=line[7:]
                    key,sep,val=line.partition('=')
                    if sep and key.strip()==name:
                        parts=shlex.split(val.strip())
                        if len(parts)==1 and parts[0]:
                            return parts[0]
            except (OSError,ValueError):
                continue
    return None

def valid_segment(dev,segment):
    if dev not in PREFIX or not isinstance(segment,str):
        return False
    return bool(re.fullmatch(
        PREFIX[dev]+r'_\d{8}T\d{6}Z_[0-9a-f]{12}',
        segment
    ))

def reconcile(dev,document,host):
    if not isinstance(document,dict):
        raise ValueError('INVALID_INVENTORY')
    if document.get('device_id') not in (None,dev):
        raise ValueError('REMOTE_DEVICE_ID_MISMATCH')

    entries=document.get('segments')
    if not isinstance(entries,list):
        raise ValueError('INVALID_SEGMENTS_ARRAY')
    if len(entries)>100000:
        raise ValueError('INVENTORY_TOO_LARGE')

    seen=set()
    counts={
        'remote_total':len(entries),
        'remote_ready':0,
        'host_metadata_match':0,
        'host_missing':0,
        'sha_mismatch':0,
        'size_mismatch':0,
        'invalid_entry':0,
        'remote_not_ready':0
    }
    sample=None

    for item in entries:
        if not isinstance(item,dict):
            counts['invalid_entry']+=1
            continue

        seg=item.get('segment_id')
        if not valid_segment(dev,seg):
            counts['invalid_entry']+=1
            continue
        if seg in seen:
            raise ValueError('DUPLICATE_REMOTE_SEGMENT')
        seen.add(seg)

        if str(item.get('state',item.get('status','UNKNOWN'))).upper()!='READY':
            counts['remote_not_ready']+=1
            continue

        size=item.get('size_bytes')
        sha=item.get('sha256')
        if type(size) is not int or size<=0 or not isinstance(sha,str):
            counts['invalid_entry']+=1
            continue
        sha=sha.lower()
        if not SHA.fullmatch(sha):
            counts['invalid_entry']+=1
            continue

        counts['remote_ready']+=1
        local=host.get((dev,seg))
        if local is None:
            counts['host_missing']+=1
        elif local[0]!=sha:
            counts['sha_mismatch']+=1
        elif local[1]!=size:
            counts['size_mismatch']+=1
        else:
            counts['host_metadata_match']+=1
            if sample is None:
                sample=seg

    return {'counts':counts,'matched_sample_segment':sample}

def self_test():
    dev='cam-surya'
    seg='surya_20261101T200000Z_012345abcdef'
    sha='a'*64
    entry={'segment_id':seg,'sha256':sha,'size_bytes':100,'state':'READY'}
    host={(dev,seg):(sha,100)}

    def run(item,db=host):
        return reconcile(dev,{'segments':[item]},db)['counts']

    assert run(entry)['host_metadata_match']==1
    assert run(entry,{})['host_missing']==1
    assert run({**entry,'sha256':'b'*64})['sha_mismatch']==1
    assert run({**entry,'size_bytes':101})['size_mismatch']==1
    assert run({**entry,'state':'RECORDING'})['remote_not_ready']==1
    assert run({**entry,'segment_id':'../video'})['invalid_entry']==1
    try:
        reconcile(dev,{'segments':[entry,entry]},host)
        raise AssertionError('DUPLICATE_NOT_REJECTED')
    except ValueError as exc:
        assert str(exc)=='DUPLICATE_REMOTE_SEGMENT'

    print('ENVANTER_ESLESTIRME: 7/7 PASS')
    print('PHONE_DELETE: OFF')

def main():
    if not ROOT.is_dir() or ROOT.is_symlink():
        raise RuntimeError('HDD_MISSING')

    proc=subprocess.run(
        ['findmnt','-n','-o','TARGET,FSTYPE,UUID',
         '--mountpoint',str(ROOT)],
        capture_output=True,text=True,check=True,timeout=10
    )
    if proc.stdout.strip().split()!=[str(ROOT),'ext4',UUID]:
        raise RuntimeError('HDD_UUID_MISMATCH')

    dbfile=ROOT/'meteorhub.sqlite3'
    if dbfile.is_symlink() or not dbfile.is_file():
        raise RuntimeError('UNSAFE_DATABASE')

    db=sqlite3.connect(f'file:{dbfile}?mode=ro',uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        rows=db.execute("""
          SELECT device_id,segment_id,sha256,size_bytes
          FROM segments WHERE state='INGEST_VERIFIED'
        """).fetchall()
    finally:
        db.close()

    host={}
    for dev,seg,sha,size in rows:
        if not valid_segment(dev,seg):
            continue
        key=(dev,seg)
        value=(sha,size)
        if key in host and host[key]!=value:
            raise RuntimeError('CONFLICTING_HOST_ROWS')
        host[key]=value

    config=json.loads(CONFIG.read_text())
    devices=config['devices']
    if not isinstance(devices,list):
        raise RuntimeError('INVALID_DEVICE_CONFIG')

    sys.path.insert(0,str(HUB))
    from meteornode_pull import MeteorNode, MeteorNodeLabHTTP

    result={
        'mode':'REMOTE_INVENTORY_READ_ONLY',
        'sqlite_verified_rows':len(rows),
        'active_scope':None,
        'cameras':[],
        'host_mp4_rehashed':False,
        'phone_delete':False,
        'hdd_delete':False,
        'delete_authorized':False
    }

    if SCOPE.is_file() and not SCOPE.is_symlink():
        result['active_scope']=json.loads(SCOPE.read_text()).get('night_id')

    for dev in devices:
        if not isinstance(dev,dict) or dev.get('enabled') is False:
            continue
        name=dev.get('id')
        if name not in PREFIX:
            continue

        row={'camera':name}
        result['cameras'].append(row)

        token=token_for(dev.get('token_env'))
        if not token:
            row['status']='AUTH_NOT_AVAILABLE_IN_SHELL'
            continue

        try:
            transport=dev.get('transport','tls_pinned')
            if transport=='http_lab':
                node=MeteorNodeLabHTTP(
                    dev['host'],dev['port'],token
                )
            elif transport=='tls_pinned':
                node=MeteorNode(
                    dev['host'],dev['port'],
                    dev['fingerprint'],token
                )
            else:
                raise ValueError('UNSUPPORTED_TRANSPORT')

            inventory=node.get_json('/api/v1/segments')
            row.update(reconcile(name,inventory,host))
            row['status']='INVENTORY_MATCHED'

        except Exception as exc:
            row['status']='PHONE_OR_INVENTORY_UNAVAILABLE'
            row['reason_type']=type(exc).__name__

    print(json.dumps(result,indent=2,ensure_ascii=False))
    print('REMOTE_REQUEST: GET /api/v1/segments ONLY')
    print('DELETE: OFF')

if __name__=='__main__':
    if sys.argv[1:]==['--self-test']:
        self_test()
    elif not sys.argv[1:]:
        main()
    else:
        raise SystemExit('USAGE: [--self-test]')
