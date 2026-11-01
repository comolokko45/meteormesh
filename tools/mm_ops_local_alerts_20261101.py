#!/usr/bin/env python3
"""MeteorMesh: deterministic deduplicated local event/digest generator.

NO external push, daemon, timers, phone calls, DB modifications or HDD writes.
Consumes the existing mm_ops_summary_preview JSON on stdin.
--preview: no writes, --commit: user-private small SSD state + journal lines.
This is a staged component, NOT automatically installed into systemd.
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
    return a

def actionable(a:dict) -> list[str]:
    codes=a['codes']
    chosen=sorted(set(x for x in codes if (x in ACTIONABLE or
        x.startswith('TIMER_NOT_ACTIVE:') or x.startswith('BLOCKED_')) and
        x not in NON_ACTIONABLE))
    if a['level']=='BLOCKED' and not any(x.startswith('BLOCKED_') for x in chosen):
        chosen.append('BLOCKED_UNCLASSIFIED')
    return sorted(set(chosen))

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
    raised=sorted(current-before)
    resolved=sorted(before-current)
    local=now.astimezone(TZ)
    day=local.date().isoformat()
    # If the 10 AM trigger was missed, the next existing 30-minute status tick
    # may emit that day's digest, but never send an older day's duplicate.
    digest_due=(local.hour>=10 and previous.get('last_digest_day')!=day)
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
            'night_fully_accepted':False,
            'active_alerts':sorted(current),
        }
    updated={'schema':SCHEMA,'active':sorted(current),
             'last_digest_day':day if digest_due else previous.get('last_digest_day')}
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
    ev,s4=change(bad,s3,now)
    assert not ev['new_events']
    ev,s5=change(base,s4,now)
    assert [i['code'] for i in ev['recoveries']]==['BLOCKED_HDD_NOT_VERIFIED']
    ev,s6=change(base,s5,now+dt.timedelta(days=1))
    assert ev['daily_digest']['day']=='2026-11-02'
    print('OPS_LOCAL_ALERT_SELFTEST: PASS')
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
