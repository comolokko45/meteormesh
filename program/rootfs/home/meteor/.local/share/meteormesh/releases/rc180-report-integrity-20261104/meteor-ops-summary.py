#!/usr/bin/env python3
"""MeteorMesh operational summary preview — no writes, no external notification.

Uses the pre-existing read-only MeteorHub status collector and installed
systemd user units. No new worker, timer, SQLite, or phone network request.
The source status collector may open SQLite strictly in read-only mode.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sqlite3
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

H=Path.home()
STATUS=Path('/opt/meteormesh/tools/MeteorMesh-status-Hub09.py')
BACKUPS=H/'.local/share/meteormesh/db-backups'
PHONE_STATE=H/'.local/state/meteormesh/heartbeat-watch-v2.json'
EXPECTED_UUID='444a8a6d-090a-456c-8a9d-d99665c32574'
TZ=ZoneInfo('Europe/Istanbul')
PHONE_NAMES=('viva','ruby','surya')
MORNING_REPORTS=Path('/srv/meteor-data/morning-reports')
DATA_ROOT=Path('/srv/meteor-data')
DB_PATH=DATA_ROOT/'meteorhub.sqlite3'
PHONE_PURGE_RECEIPTS=H/'.local/share/meteormesh/receipts/phone-purge.jsonl'
RETENTION_POLICY=H/'.local/state/meteormesh/negative-video-retention-enabled.json'
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
    'listener_restart':'BOUNDED_RESTART_LISTENER_ON_FRESH_CONTROL_0135_AND_HTTP_DOWN',
    'hdd_raw_delete':'DISABLED_PENDING_PER_FILE_RETIREMENT_TOMBSTONE_AND_APPROVAL',
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
                    'outage_duration_seconds','age_seconds','last_error','heartbeat_ttl_seconds',
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

SEGMENT_TIME=re.compile(r'^(?:surya|ruby|viva)_(\d{8}T\d{6}Z)_[0-9a-f]{12}$')
DEVICE_IDS={'cam-viva','cam-ruby','cam-surya'}

def _night_utc_window(night_id):
    if not isinstance(night_id,str) or not re.fullmatch(r'\d{8}-night',night_id):
        raise ValueError('INVALID_NIGHT_ID')
    day=dt.datetime.strptime(night_id[:8],'%Y%m%d').date()
    start=dt.datetime.combine(day,dt.time(19,0),TZ).astimezone(dt.timezone.utc)
    end=dt.datetime.combine(day+dt.timedelta(days=1),dt.time(5,0),TZ).astimezone(dt.timezone.utc)
    return start,end

def _segment_utc(segment_id):
    match=SEGMENT_TIME.fullmatch(segment_id) if isinstance(segment_id,str) else None
    if not match:
        return None
    try:
        return dt.datetime.strptime(match.group(1),'%Y%m%dT%H%M%SZ').replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None

def _night_job_rows(db_path,night_id):
    start,end=_night_utc_window(night_id)
    if db_path.is_symlink() or not db_path.is_file():
        return None
    uri='file:'+str(db_path)+'?mode=ro'
    with sqlite3.connect(uri,uri=True,timeout=10) as db:
        db.execute('PRAGMA query_only=ON')
        rows=db.execute("""SELECT j.device_id,j.segment_id,j.video_sha256,j.state,
                d.result_path,d.result_sha256
            FROM jobs j LEFT JOIN detector_runs d ON d.job_id=j.id
            WHERE j.kind='DETECT'""").fetchall()
        retired_table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_dispositions'").fetchone()
    selected=[]
    for row in rows:
        device,segment,video_sha,state,result_path,result_sha=row
        when=_segment_utc(segment)
        if device in DEVICE_IDS and when is not None and start<=when<end:
            selected.append((device,segment,video_sha,state,result_path,result_sha))
    return selected, retired_table is not None

def _verified_result_count(path_text,expected_sha,root):
    if not isinstance(path_text,str) or not isinstance(expected_sha,str) or not re.fullmatch(r'[0-9a-f]{64}',expected_sha):
        return None
    path=Path(path_text)
    try:
        if path.is_symlink():
            return None
        resolved=path.resolve(strict=True)
        root_resolved=root.resolve(strict=True)
        if not resolved.is_relative_to(root_resolved) or not resolved.is_file():
            return None
        fd=os.open(resolved,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        try:
            before=os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size<2 or before.st_size>16*1024*1024:
                return None
            chunks=[]
            while block:=os.read(fd,1024*1024):chunks.append(block)
            after=os.fstat(fd)
            current=os.stat(resolved,follow_symlinks=False)
            identity=lambda row:(row.st_dev,row.st_ino,row.st_size,row.st_mtime_ns)
            if identity(before)!=identity(after) or identity(after)!=identity(current):
                return None
        finally:
            os.close(fd)
        raw=b''.join(chunks)
        if len(raw)!=before.st_size:
            return None
        if hashlib.sha256(raw).hexdigest()!=expected_sha:
            return None
        doc=json.loads(raw.decode('utf-8'))
        results=doc.get('results') if isinstance(doc,dict) else None
        return len(results) if isinstance(results,list) else None
    except (OSError,ValueError,TypeError,json.JSONDecodeError):
        return None

def read_detector_summary(night_id,queue,coverage=None,*,db_path=DB_PATH,data_root=DATA_ROOT):
    """Count hash-verified detector output items for only the named local night."""
    try:
        selection=_night_job_rows(db_path,night_id)
        if selection is None:
            return {'state':'UNAVAILABLE','reason':'READ_ONLY_DB_UNAVAILABLE'}
        rows,retirement_table=selection
        counts={'WAITING':0,'RUNNING':0,'SUCCEEDED':0,'FAILED':0}
        for row in rows:
            if row[3] in counts:counts[row[3]]+=1
        verified=0
        items=0
        errors=0
        items_by_device={device:0 for device in sorted(DEVICE_IDS)}
        verified_by_device={device:0 for device in sorted(DEVICE_IDS)}
        for device,segment,video_sha,state,result_path,result_sha in rows:
            if state!='SUCCEEDED':continue
            count=_verified_result_count(result_path,result_sha,data_root)
            if count is None:
                errors+=1
            else:
                verified+=1
                items+=count
                items_by_device[device]+=count
                verified_by_device[device]+=1
        report_queue=_count_map(queue)
        queue_matches=all(report_queue.get(k,0)==counts[k] for k in counts) if report_queue else False
        three_device_coverage=(isinstance(coverage,dict) and all(
            type(coverage.get(device)) is int and coverage.get(device)>0
            for device in DEVICE_IDS))
        complete=(counts['SUCCEEDED']>0 and three_device_coverage
                  and counts['WAITING']==0 and counts['RUNNING']==0 and counts['FAILED']==0
                  and counts['SUCCEEDED']==verified and errors==0 and queue_matches)
        if errors:
            state='RESULT_INTEGRITY_REVIEW'
        elif complete and items==0:
            state='COMPLETE_ZERO_DETECTOR_ITEMS'
        elif complete:
            state='COMPLETE_UNREVIEWED_DETECTOR_ITEMS'
        elif verified or any(counts.values()):
            state='PARTIAL_ANALYSIS'
        else:
            state='NO_SCOPED_DETECTOR_RUNS'
        return {
            'state':state,'night_id':night_id,'jobs_in_night':counts,
            'verified_result_files':verified,'invalid_or_missing_result_files':errors,
            'detector_result_items_unreviewed':items,
            'detector_result_items_by_device':items_by_device,
            'verified_result_files_by_device':verified_by_device,
            'result_item_semantics':'UNREVIEWED_DETECTOR_OUTPUT_NOT_CONFIRMED_METEORS',
            'analysis_complete':complete,'three_device_coverage_proven':three_device_coverage,
            'queue_matches_report':queue_matches,
            'source_retirement_table_present':retirement_table,
        }
    except (OSError,sqlite3.Error,ValueError,TypeError):
        return {'state':'UNAVAILABLE','reason':'READ_ONLY_ANALYSIS_SUMMARY_FAILED'}

def read_phone_purge_summary(night_id,*,receipt_path=PHONE_PURGE_RECEIPTS):
    try:
        start,end=_night_utc_window(night_id)
        if receipt_path.is_symlink():
            return {'state':'UNSAFE_RECEIPT_PATH','night_id':night_id}
        try:
            st=receipt_path.lstat()
        except FileNotFoundError:
            return {'state':'NO_RECEIPTS','night_id':night_id,'deleted_by_device':{},'deleted_video_count':0,'unresolved_attempt_count':0}
        if not stat.S_ISREG(st.st_mode) or st.st_size>16*1024*1024 or st.st_mode&0o077:
            return {'state':'UNSAFE_RECEIPT_FILE','night_id':night_id}
        latest={};attempts=set();parse_errors=0
        for line in receipt_path.read_text(encoding='utf-8').splitlines():
            try:row=json.loads(line)
            except (ValueError,TypeError):
                parse_errors+=1;continue
            if not isinstance(row,dict):
                parse_errors+=1;continue
            identity=(row.get('device_id'),row.get('segment_id'),row.get('sha256'))
            when=_segment_utc(row.get('segment_id'))
            if (row.get('device_id') not in DEVICE_IDS or when is None or not start<=when<end
                    or not isinstance(row.get('sha256'),str) or not re.fullmatch(r'[0-9a-f]{64}',row['sha256'])):
                continue
            if row.get('event')=='attempt':attempts.add(identity)
            elif row.get('event')=='result':
                latest[identity]=row.get('status')
                attempts.discard(identity)
        by_device={name:0 for name in ('cam-viva','cam-ruby','cam-surya')}
        status_counts={}
        for (device,segment,digest),status in latest.items():
            status_key=str(status or 'UNKNOWN')[:64]
            status_counts[status_key]=status_counts.get(status_key,0)+1
            if status in ('deleted','video_deleted_sidecar_left'):
                by_device[device]+=1
        return {'state':'READ_WITH_ERRORS' if parse_errors else 'READ_OK',
                'night_id':night_id,'deleted_by_device':by_device,
                'deleted_video_count':sum(by_device.values()),
                'result_status_counts':status_counts,
                'unresolved_attempt_count':len(attempts),'receipt_parse_errors':parse_errors}
    except (OSError,ValueError,TypeError):
        return {'state':'UNAVAILABLE','night_id':night_id}

def read_hdd_retention_status(doc,retirement_table):
    enabled=False
    if not RETENTION_POLICY.is_symlink() and RETENTION_POLICY.is_file():
        try:
            policy=json.loads(RETENTION_POLICY.read_text(encoding='utf-8'))
            enabled=(isinstance(policy,dict) and
                     policy.get('schema')=='meteormesh.negative-retention-activation.v1' and
                     policy.get('auto_delete_enabled') is True)
        except (OSError,ValueError,TypeError):
            enabled=False
    night_id=doc.get('night_id') if isinstance(doc,dict) else None
    deleted_count=None
    deleted_bytes=None
    last_deleted=None
    if retirement_table:
        try:
            start,end=_night_utc_window(night_id)
            if DB_PATH.is_symlink() or not DB_PATH.is_file():
                raise RuntimeError('SQLITE_PATH_UNSAFE')
            uri=DB_PATH.as_uri()+'?mode=ro'
            with closing(sqlite3.connect(uri,uri=True,timeout=5)) as db:
                db.execute('PRAGMA query_only=ON')
                rows=db.execute("SELECT sd.device_id,sd.segment_id,sd.deleted_utc,"
                                "COALESCE(s.size_bytes,0) FROM source_dispositions sd "
                                "LEFT JOIN segments s ON s.device_id=sd.device_id "
                                "AND s.segment_id=sd.segment_id "
                                "WHERE sd.disposition='NO_CANDIDATE_DELETED' "
                                "AND sd.deleted_utc IS NOT NULL").fetchall()
            selected=[]
            for device,segment,deleted_utc,size_bytes in rows:
                when=_segment_utc(segment)
                if device in DEVICE_IDS and when is not None and start<=when<end:
                    selected.append((deleted_utc,size_bytes))
            deleted_count=len(selected)
            deleted_bytes=sum(max(0,int(row[1] or 0)) for row in selected)
            last_deleted=max((row[0] for row in selected if isinstance(row[0],str)),default=None)
        except (OSError,sqlite3.Error,RuntimeError,ValueError,TypeError):
            deleted_count=None
            deleted_bytes=None
            last_deleted=None
    if not retirement_table:
        state='BLOCKED_RETIREMENT_TOMBSTONE_REQUIRED'
    elif enabled and deleted_count is None:
        state='POLICY_ACTIVE_STATUS_UNAVAILABLE'
    elif enabled:
        state='BOUNDED_AUTO_DELETE_ACTIVE'
    else:
        state='DISABLED_PENDING_EXPLICIT_APPROVAL'
    return {'state':state,'auto_delete_enabled':enabled,
            'deleted_video_count':deleted_count,'deleted_video_bytes':deleted_bytes,
            'last_deleted_utc':last_deleted,'deletion_performed':bool(deleted_count),
            'retention_enabled_in_run':enabled,
            'hub_delete_enabled_in_run':enabled,
            'source_retirement_table_present':retirement_table,
            'approval_required_before_enabling':not enabled,
            'max_deletes_per_analysis_hook':5 if enabled else 0}

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
        detector_summary=read_detector_summary(night_id,queue,coverage)
        phone_purge=read_phone_purge_summary(night_id)
        hdd_retirement_table=detector_summary.get('source_retirement_table_present') is True
        hdd_retention=read_hdd_retention_status(doc,hdd_retirement_table)
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
            'detector_summary':detector_summary,
            'phone_purge':phone_purge,
            'hdd_negative_cleanup':hdd_retention,
            'meteor_candidate_count_state':'UNREVIEWED_DETECTOR_OUTPUT_ONLY',
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
            control_status=control.get('status')
            if control_status=='STALE':
                add('CONTROL_HEARTBEAT_STALE',name,
                    age_seconds=control.get('age_seconds'))
            elif control_status!='ONLINE':
                reason=('NO_SUCCESSFUL_HEARTBEAT' if control_status=='NEVER_SEEN'
                        else 'CONTROL_STATUS_'+str(control_status)[:40])
                add('CONTROL_HEARTBEAT_UNKNOWN',name,reason,
                    age_seconds=control.get('age_seconds'))
            if (network_state!='NOT_READY' and http.get('status')=='UNREACHABLE'
                    and isinstance(http.get('consecutive_failures'),int)
                    and http.get('consecutive_failures',0)>=2):
                add('API_DOWN',name,
                    failures=http.get('consecutive_failures'),
                    outage_seconds=http.get('outage_duration_seconds'))
            if (not expected and http.get('status')=='ONLINE'
                    and recording.get('status')=='RECORDING'
                    and recording.get('remote_camera_ready') is False):
                add('CAMERA_NEEDS_REARM',name,
                    reason='REARM_REQUIRED_BEFORE_NEXT_CAPTURE_WINDOW')
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
                    active_id=recording.get('active_segment_id')
                    valid_mp4=(type(completed) is int and
                               0<completed<=9_999_999_999_999)
                    valid_switch=(type(switched) is int and
                                  0<switched<=9_999_999_999_999)
                    if reason is None and (not valid_mp4 or not valid_switch or
                            not isinstance(active_id,str) or not active_id):
                        add('CAPTURE_PROGRESS_UNVERIFIED',name,
                            reason='MP4_COMPLETION_OR_SEGMENT_PROGRESS_MISSING',
                            active_segment_id=active_id,
                            last_switch_utc_ms=switched,
                            last_completed_mp4_utc_ms=completed)
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
            control_status=control.get('status')
            if control_status=='STALE':
                codes.append('CONTROL_HEARTBEAT_STALE:'+name)
            elif control_status!='ONLINE':
                codes.append('CONTROL_HEARTBEAT_UNKNOWN:'+name)
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
    unknown_phones={'state':'READ_OK','devices':{
        n:{**d,'control_013':{**d['control_013']}} for n,d in phones.items()}}
    unknown_phones['devices']['ruby']['control_013']['status']='NEVER_SEEN'
    unknown_result=review(good,{'state':'FOUND','age_hours':3.0},
        unknown_phones,timers,now=dt.datetime(2026,11,4,12,0,tzinfo=TZ))
    assert 'CONTROL_HEARTBEAT_UNKNOWN:ruby' in unknown_result['codes'],unknown_result
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
    test_phone['devices']['viva']['recording'].update({
        'recording_last_switch_utc_ms':int(dt.datetime(2026,11,4,0,5,tzinfo=TZ).timestamp()*1000),
        'recording_segment_target_ms':300_000,
        'last_completed_mp4_utc_ms':None,
        'active_segment_id':'viva_live_segment'})
    classes=issue_classifications(good,test_phone,{},test_night,{'state':'READY'},
                                  now=dt.datetime(2026,11,4,0,6,tzinfo=TZ))
    assert any(x['code']=='CAPTURE_PROGRESS_UNVERIFIED' and x.get('target')=='viva'
               for x in classes),classes
    assert not any(x['code']=='CONTROL_HEARTBEAT_STALE' for x in classes)
    test_phone['devices']['viva']['recording'].update({
        'status':'RECORDING','remote_camera_ready':False,
        'recording_last_switch_utc_ms':1_000_000,
        'recording_segment_target_ms':300_000})
    classes=issue_classifications(good,test_phone,{},None,{'state':'READY'},
                                  now=dt.datetime(2026,11,4,12,6,tzinfo=TZ))
    assert any(x['code']=='CAMERA_NEEDS_REARM' and x.get('target')=='viva'
               for x in classes),classes
    assert not any(x['code']=='CAPTURE_STALLED' for x in classes)
    test_phone['devices']['viva']['control_013']={'status':'NEVER_SEEN','age_seconds':None}
    classes=issue_classifications(good,test_phone,{},None,{'state':'READY'},
                                  now=dt.datetime(2026,11,4,12,6,tzinfo=TZ))
    assert any(x['code']=='CONTROL_HEARTBEAT_UNKNOWN' and x.get('target')=='viva'
               for x in classes),classes
    test_phone['devices']['viva']['control_013']={'status':'STALE','age_seconds':181}
    classes=issue_classifications(good,test_phone,{},None,{'state':'READY'},
                                  now=dt.datetime(2026,11,4,12,6,tzinfo=TZ))
    assert any(x['code']=='CONTROL_HEARTBEAT_STALE' and x.get('target')=='viva'
               for x in classes),classes
    assert not any(x['code']=='CONTROL_HEARTBEAT_UNKNOWN' for x in classes)
    assert not any(x['code']=='API_DOWN' for x in classes)
    assert AUTO_RECOVERY['listener_restart']=='BOUNDED_RESTART_LISTENER_ON_FRESH_CONTROL_0135_AND_HTTP_DOWN'
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp); db_path=root/'meteorhub.sqlite3'; out_dir=root/'detector-results'
        out_dir.mkdir()
        db=sqlite3.connect(db_path)
        db.executescript('''CREATE TABLE jobs (id INTEGER PRIMARY KEY,kind TEXT,device_id TEXT,
            segment_id TEXT,video_sha256 TEXT,state TEXT);
            CREATE TABLE detector_runs (job_id INTEGER,result_path TEXT,result_sha256 TEXT);''')
        rows=[('cam-viva','viva_20261103T170000Z_000000000001','SUCCEEDED',{'results':[]}),
              ('cam-ruby','ruby_20261103T170100Z_000000000002','SUCCEEDED',{'results':[{'id':'x'}]}),
              ('cam-surya','surya_20261103T170200Z_000000000003','WAITING',None)]
        for index,(device,segment,state,result) in enumerate(rows,1):
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',
                       (index,'DETECT',device,segment,'a'*64,state))
            if result is not None:
                path=out_dir/(f'{index}.json')
                raw=json.dumps(result,separators=(',',':')).encode()
                path.write_bytes(raw)
                db.execute('INSERT INTO detector_runs VALUES (?,?,?)',
                           (index,str(path),hashlib.sha256(raw).hexdigest()))
        db.commit();db.close()
        queue={'WAITING':1,'RUNNING':0,'SUCCEEDED':2,'FAILED':0}
        coverage={'cam-viva':1,'cam-ruby':1,'cam-surya':1}
        partial=read_detector_summary('20261103-night',queue,coverage,
                                      db_path=db_path,data_root=root)
        assert partial['state']=='PARTIAL_ANALYSIS' and not partial['analysis_complete']
        assert partial['detector_result_items_unreviewed']==1
        with sqlite3.connect(db_path) as db:
            db.execute("UPDATE jobs SET state='SUCCEEDED' WHERE id=3")
            path=out_dir/'3.json';raw=b'{"results":[]}'
            path.write_bytes(raw)
            db.execute('INSERT INTO detector_runs VALUES (?,?,?)',
                       (3,str(path),hashlib.sha256(raw).hexdigest()))
        complete=read_detector_summary('20261103-night',
            {'WAITING':0,'RUNNING':0,'SUCCEEDED':3,'FAILED':0},coverage,
            db_path=db_path,data_root=root)
        assert complete['state']=='COMPLETE_UNREVIEWED_DETECTOR_ITEMS'
        assert complete['analysis_complete'] and complete['detector_result_items_unreviewed']==1
        (out_dir/'1.json').write_bytes(b'{"results":[{"tampered":true}]}')
        tampered=read_detector_summary('20261103-night',
            {'WAITING':0,'RUNNING':0,'SUCCEEDED':3,'FAILED':0},coverage,
            db_path=db_path,data_root=root)
        assert tampered['state']=='RESULT_INTEGRITY_REVIEW'
        assert tampered['invalid_or_missing_result_files']==1 and not tampered['analysis_complete']
        receipt=root/'phone-purge.jsonl'
        in_night='viva_20261103T170000Z_000000000001'
        out_night='viva_20261104T150000Z_000000000004'
        digest='b'*64
        receipt.write_text('\n'.join(json.dumps(x) for x in (
            {'event':'attempt','device_id':'cam-viva','segment_id':in_night,'sha256':digest},
            {'event':'result','device_id':'cam-viva','segment_id':in_night,'sha256':digest,'status':'deleted'},
            {'event':'result','device_id':'cam-viva','segment_id':out_night,'sha256':'c'*64,'status':'deleted'}
        ))+'\n',encoding='utf-8')
        os.chmod(receipt,0o600)
        purged=read_phone_purge_summary('20261103-night',receipt_path=receipt)
        assert purged['state']=='READ_OK' and purged['deleted_video_count']==1
        assert purged['deleted_by_device']['cam-viva']==1
        assert read_hdd_retention_status({'retention_enabled':False},False)['state']=='BLOCKED_RETIREMENT_TOMBSTONE_REQUIRED'
    print('OPS_REPORT_SELFTEST: PASS; scoped detector result hashes, partial/complete state, purge receipt counts, deletion stays gated')
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
       'auto_recovery':{**AUTO_RECOVERY,'hdd_raw_delete':
           (night_status.get('hdd_negative_cleanup') or {}).get('state','UNKNOWN')},
       'queue_source':'EXISTING_READ_ONLY_STATUS_COLLECTOR',
       'notification_sent':False,'state_written':False,
       'raw_videos_touched':False,'external_phones_contacted':False},
       indent=2,ensure_ascii=False))
if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,subprocess.TimeoutExpired) as ex:
        print('OPS_SUMMARY_ERROR:',type(ex).__name__,str(ex)[:130],file=sys.stderr)
        sys.exit(2)
