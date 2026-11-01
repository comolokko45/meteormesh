#!/usr/bin/env python3
"""MeteorMesh operational summary preview — no writes, no external notification.

Uses the pre-existing read-only MeteorHub status collector and installed
systemd user units. No new worker, timer, SQLite, or phone network request.
The source status collector may open SQLite strictly in read-only mode.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from zoneinfo import ZoneInfo

H=Path.home()
STATUS=Path('/opt/meteormesh/tools/MeteorMesh-status-Hub09.py')
BACKUPS=H/'.local/share/meteormesh/db-backups'
PHONE_STATE=H/'.local/state/meteormesh/heartbeat-watch-v1.json'
EXPECTED_UUID='444a8a6d-090a-456c-8a9d-d99665c32574'
TZ=ZoneInfo('Europe/Istanbul')
UNITS=('meteormesh-status.timer','meteormesh-morning.timer',
       'meteormesh-capture.timer','meteormesh-control-013.service',
       'meteormesh-analyze.service')

def run(args,timeout=12):
    p=subprocess.run(args,text=True,capture_output=True,timeout=timeout)
    return p.returncode,p.stdout.strip()

def get_status():
    if STATUS.is_symlink() or not STATUS.is_file():
        raise RuntimeError('EXISTING_STATUS_COLLECTOR_MISSING')
    rc,out=run(['/usr/bin/python3','-B',str(STATUS),
                '--data-dir','/srv/meteor-data'],timeout=12)
    if rc!=0:
        raise RuntimeError('EXISTING_STATUS_COLLECTOR_ERROR')
    if len(out)>250000:
        raise RuntimeError('EXISTING_STATUS_REPORT_TOO_LARGE')
    return json.loads(out)

def safe_state():
    try:
        s=PHONE_STATE.lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_size>8192 or (s.st_mode&0o077):
            return {'state':'UNSAFE_STATE_FILE'}
        raw=json.loads(PHONE_STATE.read_text())
        if not isinstance(raw,dict) or raw.get('schema')!='meteormesh.heartbeat-watch.v1':
            return {'state':'UNEXPECTED_STATE_SCHEMA'}
        devices=raw.get('devices',{})
        if not isinstance(devices,dict):
            return {'state':'UNEXPECTED_DEVICE_MAP'}
        return {'state':'READ_OK','seen_online_count':sum(
            isinstance(v,dict) and v.get('seen_online') is True
            for v in devices.values()),
            'offline_alert_count':sum(
                isinstance(v,dict) and v.get('alarm_active') is True
                for v in devices.values())}
    except FileNotFoundError:
        return {'state':'FIRST_OBSERVATION_PENDING'}
    except (OSError,ValueError):
        return {'state':'UNREADABLE'}

def backup():
    if BACKUPS.is_symlink() or not BACKUPS.is_dir():
        return {'state':'BACKUP_DIR_MISSING'}
    try:
        files=[p for p in BACKUPS.glob('meteorhub-*.sqlite3')
               if p.is_file() and not p.is_symlink()]
        if not files:
            return {'state':'NO_BACKUP'}
        newest=max(files,key=lambda p:p.stat().st_mtime)
        age=(dt.datetime.now().timestamp()-newest.stat().st_mtime)/3600
        return {'state':'FOUND','age_hours':round(age,1),
                'name':newest.name}
    except OSError:
        return {'state':'ERROR_READING_BACKUP_METADATA'}

def units():
    res={}
    for name in UNITS:
        rc,out=run(['systemctl','--user','show',name,'--no-pager',
                    '-p','LoadState','-p','ActiveState',
                    '-p','UnitFileState','-p','Result','-p','ExecMainStatus'])
        props=dict(l.split('=',1) for l in out.splitlines() if '=' in l)
        res[name]={'state':props.get('ActiveState','UNKNOWN'),
                   'enabled':props.get('UnitFileState','UNKNOWN'),
                   'result':props.get('Result','UNKNOWN'),
                   'exit':props.get('ExecMainStatus','UNKNOWN')}
        if rc:res[name]['unavailable']=True
    return res

def review(status,backup_info,phone,systemd):
    codes=[]
    schema=status.get('schema')
    if (schema!='meteormesh.local-status.v1'
        or status.get('read_only') is not True
        or status.get('runs_detector') is not False
        or status.get('can_delete') is not False):
        return {'level':'UNKNOWN','codes':['STATUS_SCHEMA_OR_SAFETY_INVALID']}
    disk=status.get('hdd')
    if not isinstance(disk,dict) or not (disk.get('is_mount') is True
      and disk.get('fstype')=='ext4'
      and disk.get('uuid')==EXPECTED_UUID
      and disk.get('path')=='/srv/meteor-data'
      and disk.get('mount_verification')!='UNKNOWN'):
        codes.append('BLOCKED_HDD_NOT_VERIFIED')
    else:
        free=disk.get('free_gib')
        if not isinstance(free,(int,float)) or isinstance(free,bool):
            codes.append('BLOCKED_HDD_FREE_UNKNOWN')
        elif free<100:codes.append('LOW_DISK_BELOW_100_GIB')
        elif free<150:codes.append('DISK_CAPACITY_EARLY_WARNING')
    if backup_info['state']=='FOUND':
        if backup_info['age_hours']>36:codes.append('SSD_DB_BACKUP_OLDER_THAN_36H')
    else:codes.append('SSD_DB_BACKUP_UNKNOWN')
    for name in ('meteormesh-status.timer','meteormesh-morning.timer',
                 'meteormesh-capture.timer'):
        if systemd[name]['state']!='active':codes.append('TIMER_NOT_ACTIVE:'+name)
    if systemd['meteormesh-control-013.service']['state']!='active':
        codes.append('CONTROL_8443_NOT_ACTIVE')
    db=status.get('database',{})
    if not isinstance(db,dict) or db.get('state')!='READ_ONLY_OK':
        codes.append('DB_STATE_UNKNOWN_OR_READ_ERROR')
    # Historical FAILED is informative, not an every-30-minute new alert.
    failed=db.get('jobs',{}).get('FAILED') if isinstance(db.get('jobs'),dict) else None
    if isinstance(failed,bool) or not isinstance(failed,int):failed=None
    if isinstance(failed,int) and failed>0:
        codes.append('HISTORICAL_FAILED_JOBS_PRESENT')
    mr=status.get('last_morning',{})
    outcome=mr.get('outcome') if isinstance(mr,dict) else None
    if outcome in ('ANALYSIS_PENDING','PARTIAL_REVIEW_REQUIRED'):
        codes.append('NIGHT_NOT_FULLY_ANALYZED')
    if phone.get('offline_alert_count',0):
        codes.append('PREVIOUSLY_SEEN_PHONE_OFFLINE')
    blocking=[c for c in codes if c.startswith('BLOCKED')]
    error=any(c in ('DB_STATE_UNKNOWN_OR_READ_ERROR','CONTROL_8443_NOT_ACTIVE')
              or c.startswith('TIMER_NOT_ACTIVE:') for c in codes)
    warning=any(c not in ('HISTORICAL_FAILED_JOBS_PRESENT',
                          'NIGHT_NOT_FULLY_ANALYZED') for c in codes)
    level='BLOCKED' if blocking else 'ERROR' if error else 'WARNING' if warning else 'OK'
    if not blocking and status.get('status')=='CRITICAL':level='ERROR'
    return {'level':level,'codes':codes,
            'historical_failed_jobs':failed,
            'last_morning_outcome':outcome,
            'night_fully_accepted':False}

def self_test():
    good={'schema':'meteormesh.local-status.v1','read_only':True,
          'runs_detector':False,'can_delete':False,'status':'OK',
          'hdd':{'is_mount':True,'fstype':'ext4','uuid':EXPECTED_UUID,
                 'path':'/srv/meteor-data','free_gib':376.7},
          'database':{'state':'READ_ONLY_OK','jobs':{'FAILED':119}},
          'last_morning':{'outcome':'ANALYSIS_PENDING'}}
    timers={n:{'state':'active'} for n in UNITS}
    result=review(good,{'state':'FOUND','age_hours':3.0},
                  {'state':'FIRST_OBSERVATION_PENDING'},timers)
    assert result['level']=='OK',result
    assert result['historical_failed_jobs']==119
    assert result['night_fully_accepted'] is False
    good['hdd']['uuid']='wrong'
    assert review(good,{'state':'FOUND','age_hours':3.0},
                  {},timers)['level']=='BLOCKED'
    good['hdd']['uuid']=EXPECTED_UUID;good['hdd']['free_gib']=90
    assert 'LOW_DISK_BELOW_100_GIB' in review(good,{'state':'FOUND','age_hours':3.0},
                                              {},timers)['codes']
    print('OPS_SUMMARY_SELFTEST: PASS; NO_FILES_OR_SERVICES_TOUCHED')

def main():
    ap=argparse.ArgumentParser()
    mode=ap.add_mutually_exclusive_group()
    mode.add_argument('--self-test',action='store_true')
    mode.add_argument('--preview',action='store_true')
    args=ap.parse_args()
    if args.self_test:
        self_test();return
    stat=get_status()
    bk=backup();phone=safe_state();systemd=units()
    final=review(stat,bk,phone,systemd)
    print(json.dumps({'schema':'meteormesh.ops-summary.v1',
       'generated_tr':dt.datetime.now(TZ).isoformat(timespec='seconds'),
       'assessment':final,'hdd_free_gib':stat.get('hdd',{}).get('free_gib'),
       'backup':bk,'phone_heartbeat':phone,'units':systemd,
       'queue_source':'EXISTING_READ_ONLY_STATUS_COLLECTOR',
       'notification_sent':False,'state_written':False,
       'raw_videos_touched':False,'external_phones_contacted':False},
       indent=2,ensure_ascii=False))
if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,subprocess.TimeoutExpired) as ex:
        print('OPS_SUMMARY_ERROR:',type(ex).__name__,str(ex)[:130],file=sys.stderr)
        sys.exit(2)
