#!/usr/bin/env python3
"""Read-only MeteorMesh status -> MeteorAgent; no side-effecting actions."""
import json
import subprocess
import sys
from pathlib import Path

STATUS = Path('/opt/meteormesh/tools/MeteorMesh-status-Hub09.py')
AGENT = Path.home() / '.local/share/meteormesh/releases/rc14-20261031-status/MeteorAgent-1.2/meteor_agent_cli.py'


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
        agent = subprocess.run(
            ['/usr/bin/python3', '-B', str(AGENT), '--input', '-', '--format', 'text'],
            input=status.stdout, capture_output=True, text=True,
            timeout=15, check=False,
        )
        if agent.returncode not in (0, 1) or 'MeteorAgent 1.1 [OBSERVE]' not in agent.stdout:
            print('METEORMESH_AGENT_ERROR: invalid observe-only result', file=sys.stderr)
            return 3
        print(agent.stdout, flush=True)
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
