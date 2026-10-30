#!/usr/bin/env python3
"""Link installed MeteorAgent observe-only to existing status systemd service.
Does not enable or alter morning timer, recordings, database, or network.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

STATUS = Path('/opt/meteormesh/tools/MeteorMesh-status-Hub09.py')
HOME = Path.home()
AGENT = HOME / '.local/share/meteormesh/releases/rc12-20261030/MeteorAgent-1.1/meteor_agent_cli.py'
WRAPPER = HOME / '.local/lib/meteormesh/meteor-status-agent.py'
OVERRIDE = HOME / '.config/systemd/user/meteormesh-status.service.d/30-meteoragent-observe.conf'

WRAPPER_SOURCE = '''#!/usr/bin/env python3
"""Read-only MeteorMesh status -> MeteorAgent; no side-effecting actions."""
import json
import subprocess
import sys
from pathlib import Path

STATUS = Path('/opt/meteormesh/tools/MeteorMesh-status-Hub09.py')
AGENT = Path.home() / '.local/share/meteormesh/releases/rc12-20261030/MeteorAgent-1.1/meteor_agent_cli.py'


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
'''


def run(*args: str, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def prop(unit: str, action: str):
    return run('systemctl', '--user', action, unit).stdout.strip()


def write_new(path: Path, contents: str, mode: int) -> bool:
    if path.is_symlink():
        raise RuntimeError(f'SYMLINK_BLOCKED: {path}')
    if path.exists():
        if path.read_text() != contents:
            raise RuntimeError(f'EXISTING_CONTENT_DIFFERENT: {path}')
        return False
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(contents)
        handle.flush()
        os.fsync(handle.fileno())
    return True


def main() -> int:
    if os.geteuid() == 0:
        raise RuntimeError('DO_NOT_RUN_AS_ROOT')
    if not STATUS.is_file() or not AGENT.is_file():
        raise RuntimeError('STATUS_OR_AGENT_NOT_FOUND')
    if prop('meteormesh-morning.timer', 'is-enabled') != 'disabled' or prop('meteormesh-morning.timer', 'is-active') != 'inactive':
        raise RuntimeError('MORNING_TIMER_MUST_REMAIN_DISABLED')
    if prop('meteormesh-status.timer', 'is-enabled') != 'enabled' or prop('meteormesh-status.timer', 'is-active') != 'active':
        raise RuntimeError('EXISTING_STATUS_TIMER_NOT_ACTIVE')
    current = run('systemctl', '--user', 'show', '-p', 'ExecStart', '--value', 'meteormesh-status.service')
    if current.returncode != 0 or not current.stdout.strip():
        raise RuntimeError('STATUS_SERVICE_NOT_READABLE')
    if not (str(STATUS) in current.stdout and '--strict' in current.stdout) and str(WRAPPER) not in current.stdout:
        raise RuntimeError('UNEXPECTED_EXISTING_STATUS_EXECSTART')
    # Check no competing ExecStart override in other drop-ins.
    dropin_parent = OVERRIDE.parent
    if dropin_parent.exists():
        for other in dropin_parent.glob('*.conf'):
            if other == OVERRIDE:
                continue
            if 'ExecStart=' in other.read_text():
                raise RuntimeError(f'EXISTING_EXECSTART_OVERRIDE: {other.name}')
    print('PRECHECK: timer & source OK; morning disabled', flush=True)
    WRAPPER.parent.mkdir(parents=True, exist_ok=True)
    dropin_parent.mkdir(parents=True, exist_ok=True)
    wrapper_new = write_new(WRAPPER, WRAPPER_SOURCE, 0o600)
    # Run once without changing the service. Even WARNING exit=1 is expected.
    test = run('/usr/bin/python3', '-B', str(WRAPPER), timeout=65)
    if test.returncode not in (0, 1, 2) or 'MeteorAgent 1.1 [OBSERVE]' not in test.stdout:
        print('MANUAL_SHADOW_STDERR:', test.stderr.strip()[:300])
        raise RuntimeError('LIVE_AGENT_SHADOW_GATE_FAILED')
    print('AGENT_SHADOW: PASS', flush=True)
    print('\n'.join(test.stdout.splitlines()[:7]), flush=True)
    conf = '[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B ' + str(WRAPPER) + '\n'
    created_override = False
    try:
        created_override = write_new(OVERRIDE, conf, 0o644)
        reload = run('systemctl', '--user', 'daemon-reload', timeout=20)
        if reload.returncode != 0:
            raise RuntimeError('SYSTEMD_RELOAD_FAILED')
        effective = run('systemctl', '--user', 'show', '-p', 'ExecStart', '--value', 'meteormesh-status.service')
        if effective.returncode != 0 or str(WRAPPER) not in effective.stdout:
            raise RuntimeError('SYSTEMD_OVERRIDE_NOT_EFFECTIVE')
    except Exception:
        if created_override and OVERRIDE.is_file() and OVERRIDE.read_text() == conf:
            OVERRIDE.unlink()
            run('systemctl', '--user', 'daemon-reload', timeout=20)
        raise
    if prop('meteormesh-morning.timer', 'is-enabled') != 'disabled':
        raise RuntimeError('MORNING_TIMER_STATE_CHANGED_UNEXPECTEDLY')
    print('STATUS_AGENT: LINKED_TO_EXISTING_30MIN_TIMER')
    print('SERVICE_OVERRIDE:', OVERRIDE)
    print('MORNING_TIMER: disabled (unchanged)')
    print('VIDEOS_SQLITE_NETWORK: untouched')
    print('ROLLBACK: rm ' + str(OVERRIDE) + ' ; systemctl --user daemon-reload')
    print('===== METEORAGENT OBSERVE LINK COMPLETE =====')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print('STOP:', str(exc), file=sys.stderr)
        sys.exit(2)