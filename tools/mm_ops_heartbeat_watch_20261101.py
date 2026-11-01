#!/usr/bin/env python3
"""MeteorMesh: local phone heartbeat observer, no new daemon or timer.

Default --dry-run is strictly read-only. --commit writes only a small
transitional observation state under ~/.local/state/meteormesh; it does not
contact phones, open the production database, write to HDD or send alerts.
Intended eventually as replacement for static-IP phone watcher in the
EXISTING meteormesh-status.service, after review.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import ssl
import stat
import sys
import tempfile
from urllib import request

HOME=Path.home()
STATE=HOME/'.local/state/meteormesh/heartbeat-watch-v1.json'
CERT=HOME/'.local/share/meteormesh/control-013/server.crt'
URL='https://127.0.0.1:8443/v1/status'
DEVICES=('cam-viva','cam-ruby','cam-surya')
SCHEMA='meteormesh.heartbeat-watch.v1'

def get_devices():
    if CERT.is_symlink() or not CERT.is_file():
        raise RuntimeError('CONTROL_CERT_MISSING_OR_SYMLINK')
    ctx=ssl.create_default_context(cafile=str(CERT))
    with request.urlopen(URL, context=ctx, timeout=5) as rep:
        if rep.status!=200:raise RuntimeError('CONTROL_STATUS_HTTP_NOT_200')
        raw=rep.read(16385)
    if len(raw)>16384:raise RuntimeError('CONTROL_STATUS_TOO_LARGE')
    obj=json.loads(raw)
    if not isinstance(obj,dict) or obj.get('schema')!='meteormesh.control-status.v1':
        raise RuntimeError('CONTROL_STATUS_SCHEMA_UNEXPECTED')
    devices=obj.get('devices')
    if not isinstance(devices,dict):raise RuntimeError('DEVICE_MAP_UNEXPECTED')
    out={}
    for name in DEVICES:
        rec=devices.get(name)
        if not isinstance(rec,dict) or type(rec.get('online')) is not bool:
            raise RuntimeError('INVALID_DEVICE_STATUS_'+name)
        out[name]={'online':rec['online'],
                   'command_pending':rec.get('command_pending') is True}
    return out

def read_state():
    try:
        st=STATE.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_size>8192 or (st.st_mode&0o077):
            raise RuntimeError('UNSAFE_STATE_FILE')
        doc=json.loads(STATE.read_text(encoding='utf-8'))
        if not isinstance(doc,dict) or doc.get('schema')!=SCHEMA:
            raise RuntimeError('UNEXPECTED_STATE_SCHEMA')
        state=doc.get('devices')
        if not isinstance(state,dict):raise RuntimeError('INVALID_STATE_MAP')
        return state
    except FileNotFoundError:
        return {}

def evaluate(devices, previous):
    updates={}
    events=[]
    for name in DEVICES:
        rec=devices[name]
        earlier=previous.get(name,{})
        if not isinstance(earlier,dict):earlier={}
        seen=earlier.get('seen_online') is True
        failing=earlier.get('consecutive_offline',0)
        if not isinstance(failing,int) or not 0<=failing<=10000:failing=0
        alarm=earlier.get('alarm_active') is True
        if rec['online']:
            status='ONLINE'
            if alarm:events.append('PHONE_RECOVERED:'+name)
            elif not seen:events.append('PHONE_FIRST_HEARTBEAT:'+name)
            updates[name]={'seen_online':True,'consecutive_offline':0,'alarm_active':False}
        elif not seen:
            status='NOT_YET_SEEN'    # Real APK might not have been paired
            updates[name]={'seen_online':False,'consecutive_offline':0,'alarm_active':False}
        else:
            failing=min(10000,failing+1)
            alarming=failing>=2
            status='OFFLINE_ALERT' if alarming else 'OFFLINE_FIRST_CHECK'
            if alarming and not alarm:events.append('PHONE_OFFLINE:'+name)
            updates[name]={'seen_online':True,'consecutive_offline':failing,
                           'alarm_active':alarming}
        if rec['command_pending']:
            events.append('COMMAND_PENDING:'+name)
        rec['observed_state']=status
    return updates,events

def persist(state):
    if os.geteuid()==0:raise RuntimeError('ROOT_NOT_ALLOWED')
    if STATE.is_symlink():raise RuntimeError('STATE_SYMLINK_BLOCKED')
    parent=STATE.parent
    if parent.is_symlink():raise RuntimeError('STATE_DIRECTORY_SYMLINK')
    parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if parent.stat().st_uid!=os.getuid() or parent.stat().st_mode&0o077:
        raise RuntimeError('STATE_DIRECTORY_INSECURE')
    if STATE.exists():
        st=STATE.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_mode&0o077:
            raise RuntimeError('STATE_FILE_INSECURE')
    blob=(json.dumps({'schema':SCHEMA,'devices':state},sort_keys=True)+'\n').encode()
    fd,tmp=tempfile.mkstemp(prefix='.heartbeat-watch-',dir=parent)
    try:
        with os.fdopen(fd,'wb') as f:
            os.fchmod(f.fileno(),0o600)
            f.write(blob);f.flush();os.fsync(f.fileno())
        os.replace(tmp,STATE)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def selftest():
    alloff={d:{'online':False,'command_pending':False} for d in DEVICES}
    state,events=evaluate(alloff,{})
    assert all(alloff[d]['observed_state']=='NOT_YET_SEEN' for d in DEVICES)
    assert events==[]
    allon={d:{'online':True,'command_pending':False} for d in DEVICES}
    state,events=evaluate(allon,state)
    assert len([e for e in events if e.startswith('PHONE_FIRST_HEARTBEAT')])==3
    state,events=evaluate(alloff,state)
    assert not events and all(alloff[d]['observed_state']=='OFFLINE_FIRST_CHECK' for d in DEVICES)
    state,events=evaluate(alloff,state)
    assert len([e for e in events if e.startswith('PHONE_OFFLINE')])==3
    state,events=evaluate(alloff,state)
    assert not events
    state,events=evaluate(allon,state)
    assert len([e for e in events if e.startswith('PHONE_RECOVERED')])==3
    print('HEARTBEAT_WATCH_SELFTEST: PASS (no files/network altered)')

def main():
    p=argparse.ArgumentParser()
    mode=p.add_mutually_exclusive_group()
    mode.add_argument('--dry-run',action='store_true')
    mode.add_argument('--commit',action='store_true',
                      help='write observation state to SSD user home only')
    mode.add_argument('--self-test',action='store_true')
    args=p.parse_args()
    if args.self_test:
        selftest();return
    devices=get_devices()
    prior=read_state()
    updated,events=evaluate(devices,prior)
    now=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    print(json.dumps({'schema':SCHEMA,'timestamp_utc':now,
        'source':'LOCAL_TLS_8443_HEARTBEATS',
        'devices':devices,'events':events,
        'status':'ALL_OFFLINE_NOT_YET_SEEN' if all(
            devices[d]['observed_state']=='NOT_YET_SEEN' for d in DEVICES)
            else 'OBSERVED',
        'mode':'COMMIT_OBSERVATION' if args.commit else 'READ_ONLY_DRY_RUN',
        'production_db_opened':False,'hdd_written':False,
        'phones_contacted':False,'external_notification_sent':False},
        ensure_ascii=False,sort_keys=True,indent=2))
    if args.commit:persist(updated)

if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,ssl.SSLError,TimeoutError) as exc:
        print('HEARTBEAT_WATCH_ERROR:',type(exc).__name__,str(exc)[:100],file=sys.stderr)
        sys.exit(2)
