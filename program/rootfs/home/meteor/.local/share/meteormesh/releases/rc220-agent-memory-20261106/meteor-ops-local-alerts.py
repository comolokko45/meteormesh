#!/usr/bin/env python3
"""MeteorMesh: deterministic deduplicated local event/digest generator.

NO external push, daemon, timers, phone calls, DB modifications or HDD writes.
Consumes the existing mm_ops_summary_preview JSON on stdin.
--preview: no writes, --commit: user-private small SSD state + journal lines.
This is the existing status-service observer; it uses local journal output only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from zoneinfo import ZoneInfo

SCHEMA = 'meteormesh.ops-alert-state.v1'
SUMMARY_SCHEMA = 'meteormesh.ops-summary.v1'
TZ = ZoneInfo('Europe/Istanbul')
STATE = Path.home()/'.local/state/meteormesh/ops-alert-state-v1.json'
MAX_IN = 80_000
MAX_STATE = 8192
# Old failed jobs and pending-night information belong in daily digest,
# not actionable event alerts (the same old failure must not spam).
NON_ACTIONABLE = {
    'HISTORICAL_FAILED_JOBS_PRESENT',
    'NIGHT_NOT_FULLY_ANALYZED',
}
ACTIONABLE = {
    'NETWORK_DOWN',
    'SYNC_FAILED',
    'ANALYSIS_FAILED',
    'PHONE_HEARTBEAT_STATE_UNKNOWN',
    'BLOCKED_HDD_NOT_VERIFIED',
    'BLOCKED_HDD_FREE_UNKNOWN',
    'LOW_DISK_BELOW_100_GIB',
    'DISK_CAPACITY_EARLY_WARNING',
    'SSD_DB_BACKUP_OLDER_THAN_36H',
    'SSD_DB_BACKUP_UNKNOWN',
    'CONTROL_8443_NOT_ACTIVE',
    'DB_STATE_UNKNOWN_OR_READ_ERROR',
    'PREVIOUSLY_SEEN_PHONE_OFFLINE',
}
ACTIONABLE_PREFIXES = (
    'METEORNODE_OFFLINE_CONFIRMED:', 'CONTROL_HEARTBEAT_STALE:',
    'API_DOWN:', 'CAPTURE_STALLED:', 'STORAGE_LOW:',
    'CAMERA_NEEDS_REARM:', 'CAPTURE_PROGRESS_UNVERIFIED:',
    'HTTP_API_UNAVAILABLE:', 'CONTROL_HEARTBEAT_UNKNOWN:',
    'RECORDING_ERROR:', 'RECORDING_NOT_ACTIVE:',
    'PHONE_STORAGE_BELOW_RESERVE:', 'PHONE_STORAGE_UNKNOWN:',
)

def valid_summary(doc: object) -> dict:
    if not isinstance(doc,dict) or doc.get('schema')!=SUMMARY_SCHEMA:
        raise ValueError('INVALID_SUMMARY_SCHEMA')
    a=doc.get('assessment')
    if not isinstance(a,dict) or a.get('level') not in ('OK','WARNING','ERROR','BLOCKED'):
        raise ValueError('INVALID_ASSESSMENT')
    if a.get('night_fully_accepted') is not False:
        raise ValueError('UNEXPECTED_NIGHT_ACCEPTANCE_CLAIM')
    codes=a.get('codes')
    if not isinstance(codes,list) or len(codes)>150 or not all(
        isinstance(x,str) and 0<len(x)<160 for x in codes):
        raise ValueError('INVALID_ISSUE_CODES')
    classes=a.get('classifications',[])
    if not isinstance(classes,list) or len(classes)>60 or not all(
        isinstance(x,dict) and isinstance(x.get('code'),str)
        and len(x['code'])<80 for x in classes):
        raise ValueError('INVALID_CLASSIFICATIONS')
    return a

def actionable(a:dict) -> list[str]:
    codes=set(a['codes'])
    chosen=set(x for x in codes if (x in ACTIONABLE or
        x.startswith(ACTIONABLE_PREFIXES) or x.startswith('TIMER_NOT_ACTIVE:')
        or x.startswith('TIMER_NOT_ENABLED:') or x.startswith('BLOCKED_'))
        and x not in NON_ACTIONABLE)
    # Standard diagnoses replace older equivalent names, keeping one event open.
    for code in tuple(chosen):
        if code.startswith('CAPTURE_STALLED:'):
            device=code.split(':',1)[1]
            chosen.difference_update({
                'CAMERA_NEEDS_REARM:'+device,
                'CAPTURE_PROGRESS_UNVERIFIED:'+device,
                'RECORDING_ERROR:'+device,
                'RECORDING_NOT_ACTIVE:'+device,
            })
        elif code.startswith('API_DOWN:'):
            chosen.discard('HTTP_API_UNAVAILABLE:'+code.split(':',1)[1])
        elif code.startswith('STORAGE_LOW:'):
            target=code.split(':',1)[1]
            chosen.discard('PHONE_STORAGE_BELOW_RESERVE:'+target)
            chosen.discard('PHONE_STORAGE_UNKNOWN:'+target)
            if target=='hdd':
                chosen.discard('LOW_DISK_BELOW_100_GIB')
    if a['level']=='BLOCKED' and not any(x.startswith('BLOCKED_') for x in chosen):
        chosen.add('BLOCKED_UNCLASSIFIED')
    return sorted(chosen)


def load_state() -> dict:
    try:
        s=STATE.lstat()
    except FileNotFoundError:
        return {'schema':SCHEMA,'active':[],'last_digest_day':None}
    if not stat.S_ISREG(s.st_mode) or s.st_size>MAX_STATE or s.st_uid!=os.getuid() or s.st_mode&0o077:
        raise ValueError('UNSAFE_ALERT_STATE')
    data=json.loads(STATE.read_text(encoding='utf-8'))
    if not isinstance(data,dict) or data.get('schema')!=SCHEMA:
        raise ValueError('UNKNOWN_ALERT_STATE')
    if not isinstance(data.get('active'),list) or not all(
        isinstance(c,str) and len(c)<160 for c in data['active']):
        raise ValueError('MALFORMED_ACTIVE_CODES')
    day=data.get('last_digest_day')
    if day is not None and (not isinstance(day,str) or len(day)!=10):
        raise ValueError('MALFORMED_LAST_DIGEST_DATE')
    return data

def change(summary:dict, previous:dict, now:dt.datetime) -> tuple[dict,dict]:
    a=valid_summary(summary)
    current=set(actionable(a))
    before=set(previous['active'])
    phone=summary.get('phone_heartbeat')
    phone_read_ok=isinstance(phone,dict) and phone.get('state')=='READ_OK'
    device_rows=phone.get('devices',{}) if phone_read_ok else {}
    for name in ('viva','ruby','surya'):
        stale='CONTROL_HEARTBEAT_STALE:'+name
        unknown='CONTROL_HEARTBEAT_UNKNOWN:'+name
        row=device_rows.get(name,{}) if isinstance(device_rows,dict) else {}
        # Do not announce recovery of a storage incident from a missing or
        # stale sample; only a fresh valid observation can clear it.
        low='STORAGE_LOW:'+name
        storage=row.get('storage',{}) if isinstance(row,dict) else {}
        if low in before:
            try:
                sample=dt.datetime.fromisoformat(str(storage.get('last_sample_utc')).replace('Z','+00:00'))
                if sample.tzinfo is None:
                    raise ValueError('sample timezone missing')
                age=(now.astimezone(dt.timezone.utc)-sample.astimezone(dt.timezone.utc)).total_seconds()
                fresh=(isinstance(storage,dict) and type(storage.get('free_bytes')) is int
                       and type(storage.get('protected_reserve_bytes')) is int
                       and storage['free_bytes'] >= 0 and storage['protected_reserve_bytes'] > 0
                       and type(storage.get('reserve_ok')) is bool and 0 <= age <= 600)
            except (AttributeError,ValueError,TypeError,OverflowError):
                fresh=False
            # Open at 2 GiB in the summary, clear only above 3 GiB to avoid
            # repeated alarm/recovery around the threshold.
            if not fresh or storage['free_bytes']-storage['protected_reserve_bytes'] <= 3*1024**3:
                current.add(low)
        control=row.get('control_013',{}) if isinstance(row,dict) else {}
        status=control.get('status') if isinstance(control,dict) else None
        age=control.get('age_seconds') if isinstance(control,dict) else None
        ttl=control.get('heartbeat_ttl_seconds') if isinstance(control,dict) else None
        last=control.get('last_success_utc') if isinstance(control,dict) else None
        if type(ttl) is not int or not 1<=ttl<=3600:
            ttl=180
        fresh_online=(status=='ONLINE' and type(age) is int and 0<=age<=ttl
                      and isinstance(last,str) and bool(last))
        if fresh_online:
            current.discard(stale)
            current.discard(unknown)
        elif status=='STALE':
            current.add(stale)
            current.discard(unknown)
        elif phone_read_ok:
            current.add(unknown)
            current.discard(stale)
        else:
            # A missing or unreadable observation cannot close an open incident.
            if stale in before:
                current.add(stale)
            if unknown in before:
                current.add(unknown)
        if stale in before and unknown in current:
            before.discard(stale)
            before.add(unknown)
        elif unknown in before and stale in current:
            before.discard(unknown)
            before.add(stale)
    # When a live summary adopts a standard issue code, migrate the matching
    # legacy code silently so one continuing fault is not both recovered/raised.
    for code in tuple(current):
        if code.startswith('CAPTURE_STALLED:'):
            suffix=code.split(':',1)[1]
            aliases={'CAMERA_NEEDS_REARM:'+suffix,
                     'CAPTURE_PROGRESS_UNVERIFIED:'+suffix,
                     'RECORDING_ERROR:'+suffix,'RECORDING_NOT_ACTIVE:'+suffix}
            if before & aliases:
                before.difference_update(aliases); before.add(code)
        elif code.startswith('API_DOWN:'):
            alias='HTTP_API_UNAVAILABLE:'+code.split(':',1)[1]
            if alias in before: before.discard(alias); before.add(code)
        elif code.startswith('STORAGE_LOW:'):
            suffix=code.split(':',1)[1]
            aliases={'PHONE_STORAGE_BELOW_RESERVE:'+suffix,
                     'PHONE_STORAGE_UNKNOWN:'+suffix}
            if suffix=='hdd': aliases.add('LOW_DISK_BELOW_100_GIB')
            if before & aliases:
                before.difference_update(aliases); before.add(code)
    raised=sorted(current-before)
    resolved=sorted(before-current)
    local=now.astimezone(TZ)
    day=local.date().isoformat()
    # The existing morning service calls the status observer after its sync;
    # the first status tick at/after 09:00 emits one digest for that local day.
    digest_due=(local.hour>=9 and previous.get('last_digest_day')!=day)
    event={
        'schema':'meteormesh.ops-local-events.v1',
        'generated_tr':local.isoformat(timespec='seconds'),
        'health':a['level'],
        'new_events':[{'type':'ALERT','code':c} for c in raised],
        'recoveries':[{'type':'RECOVERED','code':c} for c in resolved],
        'daily_digest':None,
        'external_delivery':'DISABLED',
        'phone_commands_sent':False,
        'raw_video_deleted':False,
    }
    if digest_due:
        backup=summary.get('backup',{})
        event['daily_digest']={
            'day':day,
            'health':a['level'],
            'hdd_free_gib':summary.get('hdd_free_gib'),
            'backup_state':backup.get('state','UNKNOWN') if isinstance(backup,dict) else 'UNKNOWN',
            'backup_age_hours':backup.get('age_hours') if isinstance(backup,dict) else None,
            'historical_failed_jobs':a.get('historical_failed_jobs'),
            'last_morning_outcome':a.get('last_morning_outcome'),
            'phone_channels':summary.get('phone_heartbeat',{}).get('counts',{}),
            'phone_devices':summary.get('phone_heartbeat',{}).get('devices',{}),
            'night_status':summary.get('night_status',{'state':'NOT_REPORTED'}),
            'capture_progress':summary.get('capture_progress',{}),
            'network_readiness':summary.get('network_readiness',{'state':'UNKNOWN'}),
            'issue_classes':a.get('classifications',[]),
            'auto_recovery':summary.get('auto_recovery',{}),
            'night_fully_accepted':False,
            'active_alerts':sorted(current),
        }
    # Keep a small bounded incident memory in the existing private state file.
    # Old active incidents without a first-seen time remain unknown, not "new".
    prior_open=previous.get('opened_at',{})
    if not isinstance(prior_open,dict):prior_open={}
    stamp=now.astimezone(dt.timezone.utc).isoformat(timespec='seconds')
    opened={c:prior_open[c] for c in sorted(current) if c in prior_open and
            isinstance(prior_open[c],str) and len(prior_open[c])<=40}
    for c in raised:
        if len(opened)<16:opened[c]=stamp
    history=previous.get('recent_recoveries',[])
    if not isinstance(history,list):history=[]
    history=[r for r in history[-8:] if isinstance(r,dict) and
             isinstance(r.get('code'),str) and isinstance(r.get('resolved_utc'),str)]
    for c in resolved:
        duration=None
        try:
            if c in prior_open:
                start=dt.datetime.fromisoformat(prior_open[c])
                if start.tzinfo is not None:
                    duration=max(0,int((now.astimezone(dt.timezone.utc)-start.astimezone(dt.timezone.utc)).total_seconds()))
        except (ValueError,TypeError,OverflowError):pass
        history.append({'code':c,'resolved_utc':stamp,'duration_seconds':duration})
    updated={'schema':SCHEMA,'active':sorted(current),
             'last_digest_day':day if digest_due else previous.get('last_digest_day'),
             'opened_at':dict(list(opened.items())[:16]),
             'last_seen_at':{c:stamp for c in sorted(current)[:16]},
             'recent_recoveries':history[-8:]}
    return event,updated

def persist(doc:dict):
    if os.geteuid()==0:raise ValueError('NO_ROOT')
    parent=STATE.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError('STATE_PARENT_MISSING_OR_SYMLINK')
    s=parent.stat()
    if s.st_uid!=os.getuid() or s.st_mode&0o077:
        raise ValueError('STATE_PARENT_INSECURE')
    if STATE.is_symlink():
        raise ValueError('STATE_FILE_SYMLINK')
    if STATE.exists():
        old=STATE.lstat()
        if not stat.S_ISREG(old.st_mode) or old.st_uid!=os.getuid() or old.st_mode&0o077:
            raise ValueError('STATE_FILE_UNSAFE')
    raw=(json.dumps(doc,sort_keys=True,separators=(',',':'))+'\n').encode()
    if len(raw)>MAX_STATE:raise ValueError('STATE_OVERSIZE')
    fd,tmp=tempfile.mkstemp(dir=parent,prefix='.ops-alert-')
    try:
        with os.fdopen(fd,'wb') as f:
            os.fchmod(f.fileno(),0o600)
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,STATE)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def selftest():
    now=dt.datetime(2026,11,1,13,30,tzinfo=TZ)
    base={'schema':SUMMARY_SCHEMA,'assessment':{
        'level':'WARNING',
        'codes':['HISTORICAL_FAILED_JOBS_PRESENT','NIGHT_NOT_FULLY_ANALYZED'],
        'historical_failed_jobs':119,'last_morning_outcome':'ANALYSIS_PENDING',
        'night_fully_accepted':False},
        'hdd_free_gib':376.76,'backup':{'state':'FOUND','age_hours':3.4}}
    empty={'schema':SCHEMA,'active':[],'last_digest_day':None}
    ev,s1=change(base,empty,now)
    assert ev['new_events']==[] and ev['daily_digest']['historical_failed_jobs']==119
    ev,s2=change(base,s1,now)
    assert ev['new_events']==[] and ev['daily_digest'] is None
    bad=json.loads(json.dumps(base))
    bad['assessment']['level']='BLOCKED'
    bad['assessment']['codes']+=['BLOCKED_HDD_NOT_VERIFIED']
    ev,s3=change(bad,s2,now)
    assert [i['code'] for i in ev['new_events']]==['BLOCKED_HDD_NOT_VERIFIED']
    assert s3['opened_at']['BLOCKED_HDD_NOT_VERIFIED']==now.astimezone(dt.timezone.utc).isoformat(timespec='seconds')
    ev,s4=change(bad,s3,now)
    assert not ev['new_events']
    ev,s5=change(base,s4,now)
    assert [i['code'] for i in ev['recoveries']]==['BLOCKED_HDD_NOT_VERIFIED']
    assert s5['recent_recoveries'][-1]['duration_seconds']==0
    assert 'BLOCKED_HDD_NOT_VERIFIED' not in s5['opened_at']
    _,s_delayed=change(base,s4,now+dt.timedelta(minutes=10))
    assert s_delayed['recent_recoveries'][-1]['duration_seconds']==600
    legacy={'schema':SCHEMA,'active':['BLOCKED_HDD_NOT_VERIFIED'],'last_digest_day':None}
    _,s_unknown=change(base,legacy,now)
    assert s_unknown['recent_recoveries'][-1]['duration_seconds'] is None
    ev,s6=change(base,s5,now+dt.timedelta(days=1))
    assert ev['daily_digest']['day']=='2026-11-02'
    morning_state={'schema':SCHEMA,'active':[],'last_digest_day':'2026-11-03'}
    morning_base=json.loads(json.dumps(base))
    morning_base['night_status']={'state':'READ_OK','detector_summary':{
        'state':'COMPLETE_ZERO_DETECTOR_ITEMS','detector_result_items_unreviewed':0}}
    morning_event,_=change(morning_base,morning_state,
        dt.datetime(2026,11,4,9,1,tzinfo=TZ))
    assert morning_event['daily_digest']['day']=='2026-11-04'
    assert morning_event['daily_digest']['night_status']['detector_summary']['state']=='COMPLETE_ZERO_DETECTOR_ITEMS'
    camera=json.loads(json.dumps(base))
    camera['assessment']['codes']=['CAMERA_NEEDS_REARM:viva','CAPTURE_PROGRESS_UNVERIFIED:viva','CAPTURE_STALLED:viva']
    assert actionable(camera['assessment'])==['CAPTURE_STALLED:viva']
    prior={'schema':SCHEMA,'active':['CAPTURE_PROGRESS_UNVERIFIED:viva'],'last_digest_day':'2026-11-01'}
    migrated,_=change(camera,prior,now)
    assert migrated['new_events']==[] and migrated['recoveries']==[]
    ev,s7=change(camera,empty,now)
    assert [i['code'] for i in ev['new_events']]==['CAPTURE_STALLED:viva']
    ev,s8=change(camera,s7,now)
    assert not ev['new_events']
    ev,s9=change(base,s8,now)
    assert [i['code'] for i in ev['recoveries']]==['CAPTURE_STALLED:viva']
    progress=json.loads(json.dumps(base))
    progress['assessment']['codes']=['CAPTURE_PROGRESS_UNVERIFIED:viva']
    ev,p1=change(progress,empty,now)
    assert [i['code'] for i in ev['new_events']]==['CAPTURE_PROGRESS_UNVERIFIED:viva']
    ev,p2=change(progress,p1,now)
    assert not ev['new_events']
    ev,p3=change(base,p2,now)
    assert [i['code'] for i in ev['recoveries']]==['CAPTURE_PROGRESS_UNVERIFIED:viva']
    heartbeat_summary=json.loads(json.dumps(base))
    heartbeat_summary['assessment']['codes']=['CONTROL_HEARTBEAT_UNKNOWN:ruby']
    heartbeat_summary['phone_heartbeat']={'state':'READ_OK','devices':{
        'ruby':{'control_013':{'status':'NEVER_SEEN','age_seconds':None,
            'heartbeat_ttl_seconds':180,'last_success_utc':None}},
        'viva':{'control_013':{'status':'ONLINE','age_seconds':15,
            'heartbeat_ttl_seconds':180,'last_success_utc':'2026-11-04T12:00:00+00:00'}},
        'surya':{'control_013':{'status':'ONLINE','age_seconds':15,
            'heartbeat_ttl_seconds':180,'last_success_utc':'2026-11-04T12:00:00+00:00'}}}}
    old_stale={'schema':SCHEMA,'active':['CONTROL_HEARTBEAT_STALE:ruby'],
               'last_digest_day':'2026-11-01'}
    ev,h1=change(heartbeat_summary,old_stale,now)
    assert ev['new_events']==[] and ev['recoveries']==[]
    assert h1['active']==['CONTROL_HEARTBEAT_UNKNOWN:ruby']
    ev,h2=change(heartbeat_summary,h1,now)
    assert ev['new_events']==[] and ev['recoveries']==[]
    stale_summary=json.loads(json.dumps(heartbeat_summary))
    stale_summary['assessment']['codes']=['CONTROL_HEARTBEAT_STALE:ruby']
    stale_summary['phone_heartbeat']['devices']['ruby']['control_013']={
        'status':'STALE','age_seconds':400,'heartbeat_ttl_seconds':180,
        'last_success_utc':'2026-11-04T12:00:00+00:00'}
    ev,h3=change(stale_summary,h2,now)
    assert ev['new_events']==[] and ev['recoveries']==[]
    assert h3['active']==['CONTROL_HEARTBEAT_STALE:ruby']
    unreadable=json.loads(json.dumps(base))
    unreadable['phone_heartbeat']={'state':'UNREADABLE'}
    unreadable['assessment']['codes']=['PHONE_HEARTBEAT_STATE_UNKNOWN']
    ev,h4=change(unreadable,h3,now)
    assert ev['recoveries']==[] and 'CONTROL_HEARTBEAT_STALE:ruby' in h4['active'] and 'PHONE_HEARTBEAT_STATE_UNKNOWN' in h4['active']
    online_summary=json.loads(json.dumps(heartbeat_summary))
    online_summary['assessment']['codes']=[]
    online_summary['phone_heartbeat']['devices']['ruby']['control_013']={
        'status':'ONLINE','age_seconds':15,'heartbeat_ttl_seconds':180,
        'last_success_utc':'2026-11-04T12:00:00+00:00'}
    ev,h5=change(online_summary,h4,now)
    assert 'CONTROL_HEARTBEAT_STALE:ruby' in [x['code'] for x in ev['recoveries']]
    assert h5['active']==[]
    print('CONTROL_HEARTBEAT_UNKNOWN: retained until fresh ONLINE')
    print('CONTROL_HEARTBEAT_STALE: no false recovery on NEVER_SEEN or unreadable')
    morning=json.loads(json.dumps(base))
    morning['assessment']['classifications']=[{'code':'CAPTURE_STALLED','target':'viva'}]
    morning['night_status']={'state':'READ_OK','night_id':'20261103-night',
                             'devices':{'cam-viva':{'state':'NO_READY_SEGMENTS'}}}
    event,_=change(morning,empty,dt.datetime(2026,11,2,10,0,tzinfo=TZ))
    assert event['daily_digest']['night_status']['night_id']=='20261103-night'
    assert event['daily_digest']['issue_classes'][0]['code']=='CAPTURE_STALLED'
    print('OPS_LOCAL_ALERT_SELFTEST: PASS; CAMERA_AND_CONTROL_ALERT_TRANSITIONS')
    print('HISTORICAL_119: NO_REPEATED_ALERT')
    print('BLOCKED_AND_RECOVERED: ONE_EVENT_EACH')
    print('DAILY_DIGEST: ONE_PER_DAY')
    print('EXTERNAL_NOTIFICATIONS: DISABLED')

def main():
    p=argparse.ArgumentParser()
    opt=p.add_mutually_exclusive_group(required=True)
    opt.add_argument('--self-test',action='store_true')
    opt.add_argument('--preview',action='store_true')
    opt.add_argument('--commit',action='store_true',
                     help='Write only private SSD status state, emit journal output')
    args=p.parse_args()
    if args.self_test:
        selftest();return
    raw=sys.stdin.buffer.read(MAX_IN+1)
    if len(raw)>MAX_IN:raise ValueError('SUMMARY_INPUT_TOO_LARGE')
    summary=json.loads(raw)
    previous=load_state()
    event,nextstate=change(summary,previous,dt.datetime.now(dt.timezone.utc))
    event['mode']='LOCAL_COMMIT' if args.commit else 'READ_ONLY_PREVIEW'
    if args.commit:
        persist(nextstate)
    print(json.dumps(event,ensure_ascii=False,indent=2,sort_keys=True))
if __name__=='__main__':
    try:main()
    except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError) as e:
        print('OPS_LOCAL_ALERT_STOPPED:',type(e).__name__,str(e)[:130],
              file=sys.stderr)
        sys.exit(2)
