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
PHONE_STATE=H/'.local/state/meteormesh/heartbeat-watch-v2.json'
EXPECTED_UUID='444a8a6d-090a-456c-8a9d-d99665c32574'
TZ=ZoneInfo('Europe/Istanbul')
PHONE_NAMES=('viva','ruby','surya')
MORNING_REPORTS=Path('/srv/meteor-data/morning-reports')
NETWORK_CHECK=H/'.local/lib/meteormesh/meteor-network-ready.py'
TIMER_UNITS=('meteormesh-status.timer','meteormesh-morning.timer',
             'meteormesh-capture.timer','meteormesh-hourly-pull.timer',
             'meteormesh-midnight.timer','meteormesh-analysis-rounds.timer')
UNITS=(*TIMER_UNITS,'meteormesh-control-013.service',
       'meteormesh-analyze.service','meteormesh-capture.service',
       'meteormesh-hourly-pull.service','meteormesh-morning.service')
AUTO_RECOVERY={
    'capture':'EXISTING_FIVE_MINUTE_CAPTURE_TIMER',
    'transfer':'EXISTING_FIVE_MINUTE_RANGE_RESUME_AND_SHA_VERIFICATION',
    'analysis':'EXISTING_SCOPED_ANALYSIS_TIMER_AND_STALE_LEASE_RECLAIM',
    'boot':'PERSISTENT_TIMERS_AND_NETWORK_READY_GATE',
    'wifi_reconnect':'MANUAL_REQUIRED_NO_SUPPORTED_SAFE_REMOTE_WIFI_TOGGLE',
    'phone_commands_sent':False,
    'phone_purge':'EXISTING_VERIFIED_PURGE_GATES_ONLY',
    'listener_restart':'NOT_AUTOMATED_CONTROL_HEARTBEAT_REQUIRED','hdd_raw_delete':'NEVER',
}

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

def _clean_channel(raw, keys):
    if not isinstance(raw,dict):
        return {'status':'UNKNOWN'}
    out={}
    for key in keys:
        value=raw.get(key)
        if key=='status':
            out[key]=value[:64] if isinstance(value,str) else 'UNKNOWN'
        elif isinstance(value,str) and len(value)<=180:
            out[key]=value
        elif type(value) in (int,float,bool) or value is None:
            out[key]=value
    if not isinstance(out.get('status'),str):
        out['status']='UNKNOWN'
    return out

def _node_state(channels,confirmed):
    http=channels['http_api'].get('status')
    control=channels['control_013'].get('status')
    if http=='ONLINE':
        if control=='ONLINE':return 'OPERATIONAL'
        if control=='STALE':return 'OPERATIONAL_CONTROL_HEARTBEAT_STALE'
        return 'OPERATIONAL_CONTROL_HEARTBEAT_UNKNOWN'
    if control=='ONLINE':return 'HTTP_API_UNAVAILABLE_CONTROL_HEARTBEAT_ONLINE'
    if confirmed:return 'OFFLINE_CONFIRMED'
    if control=='STALE':return 'OFFLINE_PENDING_CONFIRMATION'
    return 'SERVICE_STATUS_DEGRADED'

def safe_state():
    channel_keys={
        'http_api':('status','last_success_utc','outage_started_utc',
                    'outage_duration_seconds','consecutive_failures','last_error','ip','port'),
        'control_013':('status','last_success_utc','outage_started_utc',
                    'outage_duration_seconds','age_seconds','last_error',
                    'camera_state','lan_listening','ready_segments','free_mib',
                    'app_version','command_pending'),
        'adb':('status','last_success_utc','outage_started_utc',
                    'outage_duration_seconds','consecutive_failures','last_error','ip','model'),
        'recording':('status','active_segment_id','remote_camera_ready',
                    'gap_duration_seconds',
                    'gap_started_utc','last_recording_utc','last_sample_utc',
                    'recording_last_switch_utc_ms','recording_segment_target_ms',
                    'last_completed_mp4_utc_ms','ready_segments',
                    'rotation_mode','segment_boundary_verified'),
        'storage':('status','free_bytes','free_gib','protected_reserve_bytes',
                    'reserve_ok','last_sample_utc'),
    }
    try:
        st=PHONE_STATE.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_size>32768
                or st.st_uid!=os.getuid() or st.st_mode&0o077):
            return {'state':'UNSAFE_STATE_FILE'}
        raw=json.loads(PHONE_STATE.read_text(encoding='utf-8'))
        if not isinstance(raw,dict) or raw.get('schema')!='meteormesh.heartbeat-watch.v2':
            return {'state':'UNEXPECTED_STATE_SCHEMA'}
        devices=raw.get('devices')
        if not isinstance(devices,dict) or any(n not in devices for n in PHONE_NAMES):
            return {'state':'UNEXPECTED_DEVICE_MAP'}
        result={}
        for name in PHONE_NAMES:
            source=devices.get(name)
            if not isinstance(source,dict) or type(source.get('node_offline_confirmed')) is not bool:
                return {'state':'UNEXPECTED_DEVICE_STATE'}
            src_channels=source.get('channels')
            if not isinstance(src_channels,dict):
                return {'state':'UNEXPECTED_CHANNEL_MAP'}
            channels={key:_clean_channel(src_channels.get(key),keys)
                      for key,keys in channel_keys.items()}
            confirmed=source['node_offline_confirmed']
            result[name]={'meteor_node_state':_node_state(channels,confirmed),
                          'node_offline_confirmed':confirmed,**channels}
        counts={
            'http_online':sum(d['http_api']['status']=='ONLINE' for d in result.values()),
            'meteor_node_offline_confirmed':sum(d['node_offline_confirmed'] for d in result.values()),
            'control_online':sum(d['control_013']['status']=='ONLINE' for d in result.values()),
            'control_stale':sum(d['control_013']['status']=='STALE' for d in result.values()),
            'adb_connected':sum(d['adb']['status']=='CONNECTED' for d in result.values()),
            'recording':sum(d['recording']['status']=='RECORDING' for d in result.values()),
            'storage_reserve_ok':sum(d['storage']['reserve_ok'] is True for d in result.values()),
        }
        return {'state':'READ_OK','seen_online_count':counts['http_online'],
                'offline_alert_count':counts['meteor_node_offline_confirmed'],
                'counts':counts,'devices':result}
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

def _count_map(raw):
    if not isinstance(raw,dict):
        return {}
    return {key:value for key,value in raw.items()
            if key in ('WAITING','RUNNING','SUCCEEDED','FAILED')
            and type(value) is int and 0 <= value <= 10_000_000}

def read_night_status(status):
    hdd=status.get('hdd')
    if (not isinstance(hdd,dict) or hdd.get('is_mount') is not True
            or hdd.get('uuid')!=EXPECTED_UUID
            or hdd.get('path')!='/srv/meteor-data'):
        return {'state':'BLOCKED_HDD_NOT_VERIFIED'}
    try:
        if MORNING_REPORTS.is_symlink() or not MORNING_REPORTS.is_dir():
            return {'state':'REPORT_DIR_UNAVAILABLE'}
        candidates=[]
        for path in MORNING_REPORTS.glob('morning_*.json'):
            try:
                st=path.lstat()
                if stat.S_ISREG(st.st_mode) and st.st_size<=1024*1024:
                    candidates.append((st.st_mtime,path))
            except OSError:
                continue
        if not candidates:
            return {'state':'NO_REPORT'}
        _,path=max(candidates,key=lambda row:row[0])
        doc=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(doc,dict) or doc.get('schema')!='meteormesh.morning.v0.7':
            return {'state':'UNEXPECTED_REPORT_SCHEMA'}
        night_id=doc.get('night_id')
        if not isinstance(night_id,str) or not re.fullmatch(r'\d{8}-night',night_id):
            return {'state':'INVALID_NIGHT_ID'}
        device_rows={}
        for row in doc.get('devices',[]):
            if (not isinstance(row,dict) or row.get('id') not in
                    ('cam-viva','cam-ruby','cam-surya')):
                continue
            ident=row['id']
            device_rows[ident]={
                'state':str(row.get('state','UNKNOWN'))[:80],
                'verified':row.get('verified') if type(row.get('verified')) is int else None,
                'pending_or_failed':row.get('pending_or_failed')
                    if type(row.get('pending_or_failed')) is int else None,
            }
        coverage=doc.get('night_scope_device_coverage')
        coverage={k:v for k,v in coverage.items()
                  if k in ('cam-viva','cam-ruby','cam-surya')
                  and type(v) is int and 0<=v<=10_000_000} if isinstance(coverage,dict) else {}
        queue=_count_map(doc.get('night_queue'))
        carryover=_count_map(doc.get('carryover_queue'))
        inv=doc.get('health_inventory')
        health=inv.get('device_health') if isinstance(inv,dict) else None
        detector_runs={}
        if isinstance(health,dict):
            for ident,row in health.items():
                if ident in ('cam-viva','cam-ruby','cam-surya') and isinstance(row,dict):
                    value=row.get('detector_latest_run_count')
                    if type(value) is int and 0<=value<=10_000_000:
                        detector_runs[ident]=value
        briefing=doc.get('briefing')
        real_count=briefing.get('gercek_meteor_sayisi') if isinstance(briefing,dict) else None
        return {
            'state':'READ_OK','report_name':path.name,
            'finished_utc':str(doc.get('finished_utc',''))[:40],
            'night_id':night_id,'outcome':str(doc.get('outcome','UNKNOWN'))[:80],
            'devices':device_rows,'device_coverage':coverage,
            'night_queue':queue,'carryover_queue':carryover,
            'carryover_review_required':doc.get('carryover_review_required') is True,
            'historical_queue_excluded':doc.get('historical_queue_excluded_from_night_status') is True,
            'sync_good':doc.get('sync_good') if type(doc.get('sync_good')) is int else None,
            'jobs_enqueued':doc.get('jobs_enqueued') if type(doc.get('jobs_enqueued')) is int else None,
            'sync_pending_or_failed':doc.get('sync_pending_or_failed')
                if type(doc.get('sync_pending_or_failed')) is int else None,
            'jobs_failed_this_run':doc.get('jobs_failed_this_run')
                if type(doc.get('jobs_failed_this_run')) is int else None,
            'detector_runs':detector_runs,
            'meteor_candidate_count_state':'NOT_REPORTED_AS_CANDIDATE_COUNT',
            'real_meteor_count_status':str(real_count)[:100] if isinstance(real_count,str) else 'UNKNOWN',
            'scientific_event_status':str(doc.get('scientific_event_status','UNKNOWN'))[:80],
            'errors':[x[:180] for x in doc.get('errors',[])
                      if isinstance(x,str)][:8] if isinstance(doc.get('errors'),list) else [],
        }
    except (OSError,ValueError,TypeError):
        return {'state':'UNREADABLE'}

def network_readiness():
    if NETWORK_CHECK.is_symlink() or not NETWORK_CHECK.is_file():
        return {'state':'UNKNOWN','reason':'NETWORK_CHECK_UNAVAILABLE'}
    try:
        p=subprocess.run(['/usr/bin/python3','-B',str(NETWORK_CHECK),'--timeout','0'],
            text=True,capture_output=True,timeout=12)
        output=(p.stdout+' '+p.stderr).strip()[:180]
        if p.returncode==0 and output.startswith('NETWORK_READY:'):
            return {'state':'READY','reason':output}
        if p.returncode==1 and output.startswith('NETWORK_NOT_READY:'):
            return {'state':'NOT_READY','reason':output}
        return {'state':'UNKNOWN','reason':output or 'NETWORK_CHECK_UNKNOWN'}
    except (OSError,subprocess.SubprocessError):
        return {'state':'UNKNOWN','reason':'NETWORK_CHECK_ERROR'}

def capture_progress_snapshot(phone,now=None):
    local=dt.datetime.now(TZ) if now is None else (now.astimezone(TZ) if now.tzinfo else now.replace(tzinfo=TZ))
    now_ms=int(local.timestamp()*1000)
    expected=local.hour>=19 or local.hour<5
    result={}
    devices=phone.get('devices',{}) if phone.get('state')=='READ_OK' else {}
    for name in PHONE_NAMES:
        age=None
        row=devices.get(name,{})
        rec=row.get('recording',{}) if isinstance(row,dict) else {}
        if not expected:
            state='NOT_EXPECTED'
            reason='OUTSIDE_RECORDING_WINDOW'
        elif row.get('http_api',{}).get('status')!='ONLINE':
            state='UNAVAILABLE'
            reason='HTTP_API_NOT_ONLINE'
        elif rec.get('remote_camera_ready') is False:
            state='STALLED_NEEDS_REARM'
            reason='CAMERA_NOT_READY'
        elif rec.get('status')!='RECORDING':
            state='STALLED_NOT_RECORDING' if rec.get('status') in ('ERROR','NOT_RECORDING') else 'UNVERIFIED'
            reason='RECORDING_STATE_'+str(rec.get('status','UNKNOWN'))[:50]
        else:
            switched=rec.get('recording_last_switch_utc_ms')
            target=rec.get('recording_segment_target_ms')
            threshold=max(3*target,900_000) if type(target) is int and 0<target<=3_600_000 else 900_000
            if type(switched) is int and 0<switched<=9_999_999_999_999:
                age=max(0,(now_ms-switched)//1000)
                state='STALLED_ROTATION' if age*1000>threshold else 'ROTATING_LAST_SWITCH'
                reason='SEGMENT_ROTATION_STALE' if state=='STALLED_ROTATION' else 'ACTIVE_SEGMENT_SWITCH_RECENT'
            else:
                age=None
                state='UNVERIFIED'
                reason='SEGMENT_SWITCH_TIMESTAMP_UNAVAILABLE'
        completed=rec.get('last_completed_mp4_utc_ms')
        completed_age=None
        completed_utc=None
        if type(completed) is int and 0<completed<=9_999_999_999_999:
            completed_age=max(0,(now_ms-completed)//1000)
            completed_utc=dt.datetime.fromtimestamp(completed/1000,dt.timezone.utc).isoformat(timespec='seconds')
            completed_state='FRESH' if completed_age<=900 else 'STALE'
        else:
            completed_state='NOT_EXPOSED_BY_CURRENT_APP'
        switched=rec.get('recording_last_switch_utc_ms')
        switch_utc=(dt.datetime.fromtimestamp(switched/1000,dt.timezone.utc).isoformat(timespec='seconds')
                    if type(switched) is int and 0<switched<=9_999_999_999_999 else None)
        result[name]={
            'state':state,'reason':reason,
            'active_segment_id':rec.get('active_segment_id'),
            'last_segment_switch_utc':switch_utc,
            'segment_switch_age_seconds':age,
            'segment_target_ms':rec.get('recording_segment_target_ms'),
            'last_completed_mp4_utc':completed_utc,
            'last_completed_mp4_age_seconds':completed_age,
            'last_completed_mp4_state':completed_state,
            'segment_boundary_verified':rec.get('segment_boundary_verified'),
            'rotation_mode':rec.get('rotation_mode'),
        }
    return result

def issue_classifications(status,phone,systemd,night_status,network,now=None):
    issues=[]
    def add(code,target=None,reason=None,**evidence):
        row={'code':code}
        if target is not None: row['target']=target
        if reason is not None: row['reason']=reason
        if evidence: row['evidence']=evidence
        issues.append(row)
    network_state=network.get('state') if isinstance(network,dict) else 'UNKNOWN'
    if network_state=='NOT_READY':
        add('NETWORK_DOWN','g62',network.get('reason'))
    expected=False
    if now is None:
        local=dt.datetime.now(TZ)
    else:
        local=now.astimezone(TZ) if now.tzinfo else now.replace(tzinfo=TZ)
    expected=local.hour>=19 or local.hour<5
    if phone.get('state')=='READ_OK':
        for name,row in phone.get('devices',{}).items():
            http=row.get('http_api',{})
            control=row.get('control_013',{})
            recording=row.get('recording',{})
            storage=row.get('storage',{})
            if control.get('status')=='STALE':
                add('CONTROL_HEARTBEAT_STALE',name,
                    age_seconds=control.get('age_seconds'))
            if (network_state!='NOT_READY' and http.get('status')=='UNREACHABLE'
                    and isinstance(http.get('consecutive_failures'),int)
                    and http.get('consecutive_failures',0)>=2):
                add('API_DOWN',name,
                    failures=http.get('consecutive_failures'),
                    outage_seconds=http.get('outage_duration_seconds'))
            if expected and http.get('status')=='ONLINE':
                reason=None
                if recording.get('remote_camera_ready') is False:
                    reason='CAMERA_NOT_READY_NEEDS_REARM'
                elif recording.get('status') in ('ERROR','NOT_RECORDING'):
                    reason='RECORDING_NOT_ACTIVE_OR_ERROR'
                elif recording.get('status')=='RECORDING':
                    now_ms=int(local.timestamp()*1000)
                    target=recording.get('recording_segment_target_ms')
                    switched=recording.get('recording_last_switch_utc_ms')
                    completed=recording.get('last_completed_mp4_utc_ms')
                    threshold=max(3*target,900_000) if type(target) is int and 0<target<=3_600_000 else 900_000
                    for key,value,label in (
                        ('last_switch',switched,'SEGMENT_ROTATION_STALE'),
                        ('last_completed_mp4',completed,'COMPLETED_MP4_STALE')):
                        if type(value) is int and 0<value<=9_999_999_999_999:
                            age=now_ms-value
                            if age>threshold:
                                reason=label
                                break
                if reason:
                    add('CAPTURE_STALLED',name,reason,
                        active_segment_id=recording.get('active_segment_id'),
                        last_switch_utc_ms=recording.get('recording_last_switch_utc_ms'),
                        last_completed_mp4_utc_ms=recording.get('last_completed_mp4_utc_ms'))
            if storage.get('reserve_ok') is False:
                add('STORAGE_LOW',name,
                    free_gib=storage.get('free_gib'),
                    protected_reserve_bytes=storage.get('protected_reserve_bytes'))
    hdd=status.get('hdd')
    if isinstance(hdd,dict) and isinstance(hdd.get('free_gib'),(int,float)) and hdd.get('free_gib')<100:
        add('STORAGE_LOW','hdd',free_gib=hdd.get('free_gib'))
    if isinstance(night_status,dict) and night_status.get('state')=='READ_OK':
        pending=night_status.get('sync_pending_or_failed')
        if isinstance(pending,int) and pending>0:
            add('SYNC_FAILED',reason='NIGHT_SYNC_PENDING_OR_FAILED',
                pending_or_failed=pending,night_id=night_status.get('night_id'))
        queue=night_status.get('night_queue',{})
        failed=queue.get('FAILED',0) if isinstance(queue,dict) else 0
        if isinstance(failed,int) and failed>0:
            add('ANALYSIS_FAILED',reason='CURRENT_NIGHT_QUEUE_FAILED',
                failed=failed,night_id=night_status.get('night_id'))
    pull=systemd.get('meteormesh-hourly-pull.service',{})
    if (pull.get('result')=='failed' and pull.get('state') not in ('active','activating')
            and not any(x.get('code')=='SYNC_FAILED' for x in issues)):
        add('SYNC_FAILED',reason='HOURLY_PULL_SERVICE_FAILED',
            exit=pull.get('exit'))
    return issues

def review(status,backup_info,phone,systemd,night_status=None,network=None,now=None):
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
    for name in TIMER_UNITS:
        row=systemd.get(name,{})
        if row.get('state')!='active':
            codes.append('TIMER_NOT_ACTIVE:'+name)
        elif row.get('enabled') not in ('enabled','enabled-runtime'):
            codes.append('TIMER_NOT_ENABLED:'+name)
    if systemd.get('meteormesh-control-013.service',{}).get('state')!='active':
        codes.append('CONTROL_8443_NOT_ACTIVE')
    db=status.get('database',{})
    if not isinstance(db,dict) or db.get('state')!='READ_ONLY_OK':
        codes.append('DB_STATE_UNKNOWN_OR_READ_ERROR')
    failed=db.get('jobs',{}).get('FAILED') if isinstance(db.get('jobs'),dict) else None
    if isinstance(failed,bool) or not isinstance(failed,int):failed=None
    if isinstance(failed,int) and failed>0:
        codes.append('HISTORICAL_FAILED_JOBS_PRESENT')
    mr=status.get('last_morning',{})
    outcome=mr.get('outcome') if isinstance(mr,dict) else None
    if outcome in ('ANALYSIS_PENDING','PARTIAL_REVIEW_REQUIRED'):
        codes.append('NIGHT_NOT_FULLY_ANALYZED')

    if phone.get('state')!='READ_OK':
        codes.append('PHONE_HEARTBEAT_STATE_UNKNOWN')
    else:
        local=now.astimezone(TZ) if now is not None else dt.datetime.now(TZ)
        recording_expected=(local.hour>=19 or local.hour<5)
        start_grace=(local.hour==19 and local.minute<10)
        for name,d in phone.get('devices',{}).items():
            http=d.get('http_api',{})
            control=d.get('control_013',{})
            recording=d.get('recording',{})
            storage=d.get('storage',{})
            if d.get('node_offline_confirmed') is True:
                codes.append('METEORNODE_OFFLINE_CONFIRMED:'+name)
            if control.get('status')=='STALE':
                codes.append('CONTROL_HEARTBEAT_STALE:'+name)
            failures=http.get('consecutive_failures',0)
            if (http.get('status')=='UNREACHABLE' and isinstance(failures,int)
                    and failures>=2 and d.get('node_offline_confirmed') is not True):
                codes.append('HTTP_API_UNAVAILABLE:'+name)
            if http.get('status')=='ONLINE':
                if recording.get('status')=='ERROR':
                    codes.append('RECORDING_ERROR:'+name)
                if (recording_expected and not start_grace
                        and recording.get('status')=='NOT_RECORDING'):
                    codes.append('RECORDING_NOT_ACTIVE:'+name)
                if (recording_expected and not start_grace
                        and recording.get('remote_camera_ready') is False):
                    codes.append('CAMERA_NEEDS_REARM:'+name)
                if storage.get('reserve_ok') is False:
                    codes.append('PHONE_STORAGE_BELOW_RESERVE:'+name)
                elif storage.get('reserve_ok') is not True:
                    codes.append('PHONE_STORAGE_UNKNOWN:'+name)

    classifications=issue_classifications(status,phone,systemd,night_status,network,now)
    for issue in classifications:
        code=issue['code']+(':'+issue['target'] if issue.get('target') else '')
        if code not in codes:
            codes.append(code)
    blocking=[c for c in codes if c.startswith('BLOCKED')]
    error=any(c in ('DB_STATE_UNKNOWN_OR_READ_ERROR','CONTROL_8443_NOT_ACTIVE',
                    'PHONE_HEARTBEAT_STATE_UNKNOWN')
              or c.startswith(('TIMER_NOT_ACTIVE:','TIMER_NOT_ENABLED:')) for c in codes)
    warning=any(c!='HISTORICAL_FAILED_JOBS_PRESENT' for c in codes)
    level='BLOCKED' if blocking else 'ERROR' if error else 'WARNING' if warning else 'OK'
    if not blocking and status.get('status')=='CRITICAL':level='ERROR'
    return {'level':level,'codes':codes,
            'classifications':classifications,
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
    timers={n:{'state':'active','enabled':'enabled'} for n in TIMER_UNITS}
    timers['meteormesh-control-013.service']={'state':'active','enabled':'enabled'}
    phones={n:{'node_offline_confirmed':False,
        'http_api':{'status':'ONLINE','consecutive_failures':0},
        'control_013':{'status':'ONLINE'},
        'adb':{'status':'DISCONNECTED'},
        'recording':{'status':'RECORDING'},
        'storage':{'reserve_ok':True}} for n in PHONE_NAMES}
    result=review(good,{'state':'FOUND','age_hours':3.0},
                  {'state':'READ_OK','devices':phones},timers)
    assert result['level']=='WARNING',result
    assert result['historical_failed_jobs']==119
    assert result['night_fully_accepted'] is False
    assert not any('OFFLINE' in c for c in result['codes'])
    good['hdd']['uuid']='wrong'
    assert review(good,{'state':'FOUND','age_hours':3.0},
                  {'state':'READ_OK','devices':phones},timers)['level']=='BLOCKED'
    good['hdd']['uuid']=EXPECTED_UUID;good['hdd']['free_gib']=90
    assert 'LOW_DISK_BELOW_100_GIB' in review(
        good,{'state':'FOUND','age_hours':3.0},
        {'state':'READ_OK','devices':phones},timers)['codes']
    phones['viva']['recording']['remote_camera_ready']=False
    night=dt.datetime(2026,11,4,0,6,tzinfo=TZ)
    camera_result=review(good,{'state':'FOUND','age_hours':3.0},
                         {'state':'READ_OK','devices':phones},timers,now=night)
    assert 'CAMERA_NEEDS_REARM:viva' in camera_result['codes'],camera_result
    assert any(x['code']=='CAPTURE_STALLED' and x.get('target')=='viva'
               for x in camera_result['classifications']),camera_result
    phones['viva']['recording']['remote_camera_ready']=True
    camera_result=review(good,{'state':'FOUND','age_hours':3.0},
                         {'state':'READ_OK','devices':phones},timers,now=night)
    assert 'CAMERA_NEEDS_REARM:viva' not in camera_result['codes'],camera_result
    test_phone={'state':'READ_OK','devices':{
        'viva':{'node_offline_confirmed':False,
            'http_api':{'status':'ONLINE','consecutive_failures':0},
            'control_013':{'status':'ONLINE'},
            'adb':{'status':'DISCONNECTED'},
            'recording':{'status':'RECORDING','remote_camera_ready':True,
                'recording_last_switch_utc_ms':1_000_000,
                'recording_segment_target_ms':300_000},
            'storage':{'reserve_ok':True}},
    }}
    test_night={'state':'READ_OK','night_id':'20261103-night',
                'sync_pending_or_failed':2,'night_queue':{'FAILED':1}}
    classes=issue_classifications(good,test_phone,{},test_night,{'state':'READY'},
                                  now=dt.datetime(2026,11,4,0,6,tzinfo=TZ))
    assert {x['code'] for x in classes}=={'CAPTURE_STALLED','STORAGE_LOW','SYNC_FAILED','ANALYSIS_FAILED'},classes
    assert not any(x['code']=='CONTROL_HEARTBEAT_STALE' for x in classes)
    assert not any(x['code']=='API_DOWN' for x in classes)
    print('OPS_SUMMARY_SELFTEST: PASS; independent channels; segment progress; night-scoped sync/analysis classification')

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
    night_status=read_night_status(stat);network=network_readiness()
    final=review(stat,bk,phone,systemd,night_status,network)
    print(json.dumps({'schema':'meteormesh.ops-summary.v1',
       'generated_tr':dt.datetime.now(TZ).isoformat(timespec='seconds'),
       'assessment':final,'hdd_free_gib':stat.get('hdd',{}).get('free_gib'),
       'backup':bk,'phone_heartbeat':phone,'units':systemd,
       'night_status':night_status,'network_readiness':network,
       'capture_progress':capture_progress_snapshot(phone),
       'auto_recovery':AUTO_RECOVERY,'queue_source':'EXISTING_READ_ONLY_STATUS_COLLECTOR',
       'notification_sent':False,'state_written':False,
       'raw_videos_touched':False,'external_phones_contacted':False},
       indent=2,ensure_ascii=False))
if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,subprocess.TimeoutExpired) as ex:
        print('OPS_SUMMARY_ERROR:',type(ex).__name__,str(ex)[:130],file=sys.stderr)
        sys.exit(2)
