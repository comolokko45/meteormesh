#!/usr/bin/env python3
"""Read-only MeteorMesh status -> MeteorAgent; no side-effecting actions."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATUS = BASE / 'MeteorMesh-status-Hub09.py'
AGENT = BASE / 'MeteorAgent-1.2/meteor_agent_cli.py'
ALERT_STATE = Path.home() / '.local/state/meteormesh/ops-alert-state-v1.json'


def read_local_alerts():
    try:
        st = ALERT_STATE.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or st.st_mode & 0o077 or st.st_size > 8192):
            return {'state': 'UNAVAILABLE', 'active': []}
        obj = json.loads(ALERT_STATE.read_text(encoding='utf-8'))
        codes = obj.get('active') if isinstance(obj, dict) else None
        if (not isinstance(obj, dict) or obj.get('schema') != 'meteormesh.ops-alert-state.v1'
                or not isinstance(codes, list) or len(codes) > 150
                or not all(isinstance(code, str) and 0 < len(code) < 160 for code in codes)):
            return {'state': 'UNAVAILABLE', 'active': []}
        opened=obj.get('opened_at',{})
        last_seen=obj.get('last_seen_at',{})
        history=obj.get('recent_recoveries',[])
        if not isinstance(opened,dict) or not isinstance(last_seen,dict) or not isinstance(history,list):
            opened={};last_seen={};history=[]
        return {'state': 'READ_OK', 'active': codes,
                'opened_at':{k:v for k,v in opened.items() if k in codes and
                             isinstance(v,str) and len(v)<=40},
                'last_seen_at':{k:v for k,v in last_seen.items() if k in codes and
                                isinstance(v,str) and len(v)<=40},
                'recent_recoveries':[r for r in history[-8:] if isinstance(r,dict)
                                      and isinstance(r.get('code'),str)]}
    except (OSError, ValueError, TypeError):
        return {'state': 'UNAVAILABLE', 'active': []}


def main():
    try:
        status = subprocess.run(
            ['/usr/bin/python3', '-B', str(STATUS), '--data-dir', '/srv/meteor-data', '--strict'],
            capture_output=True, text=True, timeout=45, check=False,
        )
        report = json.loads(status.stdout)
        if not isinstance(report, dict) or report.get('schema') != 'meteormesh.local-status.v1':
            raise ValueError('Invalid status schema')
        if report.get('read_only') is not True or report.get('can_delete') is not False:
            raise ValueError('Unsafe status schema')
        # This is the last committed local incident state; the watcher and
        # journal run after this service's main command on the existing timer.
        report['local_alerts'] = read_local_alerts()
        agent = subprocess.run(
            ['/usr/bin/python3', '-B', str(AGENT), '--input', '-', '--format', 'text'],
            input=json.dumps(report, ensure_ascii=False), capture_output=True, text=True,
            timeout=15, check=False,
        )
        if agent.returncode not in (0, 1) or 'MeteorAgent AutoPilot 1.2 [OBSERVE]' not in agent.stdout:
            print('METEORMESH_AGENT_ERROR: invalid observe-only result', file=sys.stderr)
            return 3
        print(agent.stdout, flush=True)
        if report['local_alerts']['active']:
            print('METEORAGENT_ACTIVE_OPS_ALERTS: ' + ', '.join(report['local_alerts']['active']), flush=True)
        for code,stamp in report['local_alerts'].get('opened_at',{}).items():
            print('METEORAGENT_INCIDENT_OPENED: '+code+' '+stamp,flush=True)
        for code,stamp in report['local_alerts'].get('last_seen_at',{}).items():
            print('METEORAGENT_INCIDENT_LAST_SEEN: '+code+' '+stamp,flush=True)
        for row in report['local_alerts'].get('recent_recoveries',[]):
            print('METEORAGENT_INCIDENT_RECOVERED: '+str(row.get('code',''))[:160]+
                  ' duration_seconds='+str(row.get('duration_seconds')),flush=True)
        print('METEORMESH_STATUS_STRICT_EXIT:', status.returncode, flush=True)
        if status.returncode not in (0, 1, 2):
            print('METEORMESH_STATUS_ERROR: unexpected collector return code', file=sys.stderr)
            return 3
        # Preserve old --strict exit semantics: warning=1, critical=2.
        return status.returncode
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print('METEORMESH_OBSERVE_ERROR: status/agent unavailable; no action taken', file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())
