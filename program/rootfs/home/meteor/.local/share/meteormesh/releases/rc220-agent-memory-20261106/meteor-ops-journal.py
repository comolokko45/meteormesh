#!/usr/bin/env python3
"""Run existing MeteorMesh operations summary -> local alerts, without shell.

Intended only as a second, nonblocking ExecStartPost on the EXISTING
meteormesh-status.service. No daemon, new timer, DB writer, phone polling,
remote push, camera control, video operations, or automatic remediation.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys

BASE=Path.home()/'.local/lib/meteormesh'
SUMMARY=BASE/'meteor-ops-summary.py'
ALERTS=Path.home()/'.local/share/meteormesh/releases/rc220-agent-memory-20261106/meteor-ops-local-alerts.py'

def execute():
    for path in (SUMMARY,ALERTS):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('MISSING_OR_SYMLINK_SOURCE: '+path.name)
    summary=subprocess.run(['/usr/bin/python3','-B',str(SUMMARY),'--preview'],
                           capture_output=True,text=True,timeout=25)
    if summary.returncode or len(summary.stdout)>80000:
        raise RuntimeError('SUMMARY_FAILED')
    parsed=json.loads(summary.stdout)
    if parsed.get('schema')!='meteormesh.ops-summary.v1':
        raise RuntimeError('UNEXPECTED_SUMMARY_SCHEMA')
    if parsed.get('notification_sent') is not False or parsed.get('state_written') is not False:
        raise RuntimeError('UNSAFE_SUMMARY_RESULT')
    alerts=subprocess.run(['/usr/bin/python3','-B',str(ALERTS),'--commit'],
                          input=summary.stdout,capture_output=True,text=True,timeout=15)
    if alerts.returncode or len(alerts.stdout)>80000:
        raise RuntimeError('LOCAL_ALERT_COMMIT_FAILED: '+alerts.stderr[:140])
    obj=json.loads(alerts.stdout)
    if obj.get('schema')!='meteormesh.ops-local-events.v1' or obj.get('mode')!='LOCAL_COMMIT':
        raise RuntimeError('UNEXPECTED_EVENT_SCHEMA')
    if obj.get('external_delivery')!='DISABLED' or obj.get('phone_commands_sent') is not False or obj.get('raw_video_deleted') is not False:
        raise RuntimeError('UNSAFE_EVENT_RESPONSE')
    for item in obj.get('new_events',[]):
        print('METEORMESH_OPS_ALERT: '+str(item.get('code','UNKNOWN'))[:150],flush=True)
    for item in obj.get('recoveries',[]):
        print('METEORMESH_OPS_RECOVERED: '+str(item.get('code','UNKNOWN'))[:150],flush=True)
    if obj.get('daily_digest') is not None:
        print('METEORMESH_OPS_DAILY: '+json.dumps(obj['daily_digest'],ensure_ascii=False,
                                                 separators=(',',':')),flush=True)
    return 0

if __name__=='__main__':
    try:
        sys.exit(execute())
    except (RuntimeError,OSError,ValueError,subprocess.TimeoutExpired,KeyError) as e:
        print('METEORMESH_OPS_OBSERVER_ERROR: '+type(e).__name__+': '+str(e)[:145],flush=True)
        sys.exit(2)
