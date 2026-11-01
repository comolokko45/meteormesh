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
    warning=any(c!='HISTORICAL_FAILED_JOBS_PRESENT' for c in codes)
    level='BLOCKED' if blocking else 'ERROR' if error else 'WARNING' if warning else 'OK'
    if not blocking and status.get('status')=='CRITICAL':level='ERROR'
    return {'level':level,'codes':codes,
            'historical_failed_jobs':failed,
            'last_morning_outcome':outcome,
            'night_fully_accepted':False}


# The local journal is a state cache, not proof of external alert delivery.
# Remote notifications stay disabled unless a separate permitted sender is deployed.
LEDGER=H/'.local/state/meteormesh/ops-ledger-v1.json'
LEDGER_SCHEMA='meteormesh.ops-ledger.v1'
ALERT_CODES=frozenset((
    'BLOCKED_HDD_NOT_VERIFIED','BLOCKED_HDD_FREE_UNKNOWN',
    'LOW_DISK_BELOW_100_GIB','DISK_CAPACITY_EARLY_WARNING',
    'SSD_DB_BACKUP_OLDER_THAN_36H','SSD_DB_BACKUP_UNKNOWN',
    'CONTROL_8443_NOT_ACTIVE','DB_STATE_UNKNOWN_OR_READ_ERROR',
    'PREVIOUSLY_SEEN_PHONE_OFFLINE',
))
ALERT_PREFIXES=('TIMER_NOT_ACTIVE:',)


def active_alerts(assessment):
    """Never generate an immediate alert for stale failed jobs or a pending night."""
    return sorted(set(
        c for c in assessment.get('codes',[])
        if c in ALERT_CODES or any(c.startswith(p) for p in ALERT_PREFIXES)
    ))


def load_ledger():
    try:
        item=LEDGER.lstat()
    except FileNotFoundError:
        return {'schema':LEDGER_SCHEMA,'active':[],'digest_date':None}
    if (not stat.S_ISREG(item.st_mode) or item.st_size>8192
        or item.st_uid!=os.getuid() or item.st_mode&0o077):
        raise RuntimeError('UNSAFE_LEDGER_FILE')
    obj=json.loads(LEDGER.read_text(encoding='utf-8'))
    if (not isinstance(obj,dict) or obj.get('schema')!=LEDGER_SCHEMA
        or not isinstance(obj.get('active'),list)
        or not all(isinstance(x,str) and len(x)<150 for x in obj['active'])):
        raise RuntimeError('INVALID_LEDGER_SCHEMA')
    digest=obj.get('digest_date')
    if digest is not None and (not isinstance(digest,str)
                               or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',digest)):
        raise RuntimeError('INVALID_LEDGER_DIGEST_DATE')
    return {'schema':LEDGER_SCHEMA,'active':sorted(set(obj['active'])),
            'digest_date':digest}


def calculate_events(assessment,prior,now):
    active=active_alerts(assessment)
    previously=prior['active']
    new=sorted(set(active)-set(previously))
    recovered=sorted(set(previously)-set(active))
    # One provisional operating summary per local day, after 12:00.
    date=now.date().isoformat()
    digest_due=now.hour>=12 and prior['digest_date']!=date
    updated={'schema':LEDGER_SCHEMA,'active':active,
             'digest_date':date if digest_due else prior['digest_date']}
    return {'new':new,'recovered':recovered,'daily_due':digest_due},updated


def atomic_ledger(doc):
    if os.geteuid()==0:raise RuntimeError('ROOT_FORBIDDEN')
    folder=LEDGER.parent
    if folder.is_symlink() or not folder.is_dir():
        raise RuntimeError('LEDGER_PARENT_MISSING_OR_SYMLINK')
    st=folder.stat()
    if st.st_uid!=os.getuid() or st.st_mode&0o077:
        raise RuntimeError('LEDGER_PARENT_NOT_PRIVATE')
    if LEDGER.is_symlink():raise RuntimeError('LEDGER_SYMLINK')
    if LEDGER.exists():
        existing=LEDGER.lstat()
        if not stat.S_ISREG(existing.st_mode) or existing.st_uid!=os.getuid():
            raise RuntimeError('LEDGER_ALREADY_UNSAFE')
    import tempfile
    blob=(json.dumps(doc,sort_keys=True,separators=(',',':'))+'\n').encode()
    fd,path=tempfile.mkstemp(prefix='.ops-ledger-',dir=folder)
    try:
        with os.fdopen(fd,'wb') as stream:
            os.fchmod(stream.fileno(),0o600)
            stream.write(blob);stream.flush();os.fsync(stream.fileno())
        os.replace(path,LEDGER)
        dfd=os.open(folder,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        if os.path.exists(path):os.unlink(path)


def print_decisions(events,summary,now):
    for code in events['new']:
        print('METEORMESH_LOCAL_ALERT: '+code,flush=True)
    for code in events['recovered']:
        print('METEORMESH_LOCAL_RECOVERED: '+code,flush=True)
    if events['daily_due']:
        assessment=summary['assessment']
        print('METEORMESH_DAILY_SUMMARY: '+json.dumps({
            'date_tr':now.date().isoformat(),
            'health':assessment['level'],
            'active_alerts':active_alerts(assessment),
            'last_morning_outcome':assessment.get('last_morning_outcome'),
            'night_fully_accepted':False,
            'historical_failed_jobs':assessment.get('historical_failed_jobs'),
            'hdd_free_gib':summary.get('hdd_free_gib'),
            'backup':summary.get('backup',{}).get('state'),
            'phone_observation':summary.get('phone_heartbeat',{}).get('state'),
            'provisional':True,
        },sort_keys=True),flush=True)
    if not events['new'] and not events['recovered'] and not events['daily_due']:
        print('METEORMESH_OPS_NO_NEW_LOCAL_EVENTS',flush=True)


def ledger_self_test():
    def t(h):
        return dt.datetime(2026,11,1,h,0,tzinfo=TZ)
    base={'schema':LEDGER_SCHEMA,'active':[],'digest_date':None}
    assessment={'codes':['HISTORICAL_FAILED_JOBS_PRESENT',
                         'NIGHT_NOT_FULLY_ANALYZED']}
    e,s=calculate_events(assessment,base,t(13))
    assert not e['new'] and e['daily_due'] and not e['recovered']
    e,s=calculate_events(assessment,s,t(13))
    assert not e['new'] and not e['daily_due']
    assessment['codes']+=['LOW_DISK_BELOW_100_GIB']
    e,s=calculate_events(assessment,s,t(13))
    assert e['new']==['LOW_DISK_BELOW_100_GIB']
    e,s=calculate_events(assessment,s,t(13))
    assert not e['new']
    assessment['codes'].remove('LOW_DISK_BELOW_100_GIB')
    e,s=calculate_events(assessment,s,t(13))
    assert e['recovered']==['LOW_DISK_BELOW_100_GIB']
    e,s=calculate_events(assessment,s,dt.datetime(2026,11,2,12,tzinfo=TZ))
    assert e['daily_due']
    assert not active_alerts({'codes':['HISTORICAL_FAILED_JOBS_PRESENT']})
    print('OPS_LEDGER_SELFTEST: PASS; NO_FILES_OR_SERVICES_TOUCHED')


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
    assert result['level']=='WARNING',result
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
    mode.add_argument('--commit-local',action='store_true',help='SSD-only local event ledger, no push')
    args=ap.parse_args()
    if args.self_test:
        self_test();ledger_self_test();return
    stat=get_status()
    bk=backup();phone=safe_state();systemd=units()
    final=review(stat,bk,phone,systemd)
    now=dt.datetime.now(TZ)
    prior=load_ledger()
    events,next_ledger=calculate_events(final,prior,now)
    if args.commit_local:
        atomic_ledger(next_ledger)
    print_decisions(events,{'assessment':final,'hdd_free_gib':stat.get('hdd',{}).get('free_gib'),
                           'backup':bk,'phone_heartbeat':phone},now)
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
