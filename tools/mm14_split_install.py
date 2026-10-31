#!/usr/bin/env python3
"""MeteorMesh RC1.4: separate morning ingest and queued analysis (no source data deletion).

Installs a new versioned MeteorHub copy and two systemd user service definitions.
Does not start services or change timers. Runs as regular meteor user.
"""
from __future__ import annotations
import argparse
import ast
import datetime
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

HOME=Path.home()
RELEASES=HOME/'.local/share/meteormesh/releases'
SRC=RELEASES/'rc13-20261030-carryover/MeteorHub'
DST=RELEASES/'rc14-20261031-split/MeteorHub'
DROP_DIR=HOME/'.config/systemd/user/meteormesh-morning.service.d'
OLD_DROP=DROP_DIR/'95-meteormesh-rc13-carryover.conf'
NEW_DROP=DROP_DIR/'97-meteormesh-rc14-split.conf'
ANALYSIS=HOME/'.config/systemd/user/meteormesh-analyze.service'


def patch(s: str)->str:
    def one(before: str, after: str):
        nonlocal s
        if s.count(before)!=1:
            raise RuntimeError('PATCH_ANCHOR_MISMATCH: '+repr(before[:65]))
        s=s.replace(before,after,1)

    one('                night_scope_path: Path | None = None) -> dict:',
        '                night_scope_path: Path | None = None,\n                phase: str = "both") -> dict:')
    one('    scope = load_night_scope(night_scope_path) if night_scope_path is not None else None\n',
        '    if phase not in ("both", "sync", "analyze"):\n        raise ValueError("Invalid phase")\n'
        '    scope = load_night_scope(night_scope_path) if night_scope_path is not None else None\n')
    one('        "max_segments_per_device": max_segments_per_device,\n',
        '        "max_segments_per_device": max_segments_per_device,\n'
        '        "phase": phase,\n')
    a='            for device in devices:\n'
    b='            detector_ready = False\n'
    if s.count(a)!=1 or s.count(b)!=1:
        raise RuntimeError('SYNC_BLOCK_ANCHOR_MISMATCH')
    i=s.index(a); j=s.index(b,i)
    block=s[i:j]
    s=s[:i]+'            if phase != "analyze":\n'+''.join('    '+line if line.strip() else line for line in block.splitlines(keepends=True))+s[j:]
    one('                    verify_metdet_repo(detector_repo)\n',
        '                    if phase != "sync":\n'
        '                        verify_metdet_repo(detector_repo)\n')
    one('            if detector_ready:\n',
        '            if detector_ready and phase != "sync":\n')
    one('            if verified_total and all_devices_ok and all_jobs_terminal_success and detector_ready:\n',
        '            if phase != "sync" and verified_total and all_devices_ok and all_jobs_terminal_success and detector_ready:\n')
    one('                report["provisional_events_skipped"] = "INGEST_OR_ANALYSIS_INCOMPLETE"\n',
        '                report["provisional_events_skipped"] = (\n'
        '                    "ANALYSIS_DEFERRED" if phase == "sync" else "INGEST_OR_ANALYSIS_INCOMPLETE")\n')
    one('        report["outcome"] = "PARTIAL_REVIEW_REQUIRED" if (\n'
        '            report["errors"] or report.get("provisional_events_skipped")) else "COMPLETED_PROVISIONAL"\n',
        '        if phase == "sync":\n'
        '            report["outcome"] = "PARTIAL_REVIEW_REQUIRED" if report["errors"] else "INGEST_QUEUED"\n'
        '        elif phase == "analyze":\n'
        '            scoped = (report.get("night_queue") or report["queue"]["jobs"])\n'
        '            carryover = report.get("carryover_queue") or {}\n'
        '            pending = any(x.get(k, 0) for x in (scoped, carryover)\n'
        '                          for k in ("WAITING", "RUNNING", "FAILED"))\n'
        '            report["outcome"] = "ANALYSIS_PENDING" if (report["errors"] or pending) else "ANALYSIS_COMPLETE_PROVISIONAL"\n'
        '        else:\n'
        '            report["outcome"] = "PARTIAL_REVIEW_REQUIRED" if (\n'
        '                report["errors"] or report.get("provisional_events_skipped")) else "COMPLETED_PROVISIONAL"\n')
    one('            p.add_argument("--night-scope", type=Path, default=None, help="Restrict ingest and queue to explicit night policy")\n',
        '            p.add_argument("--night-scope", type=Path, default=None, help="Restrict ingest and queue to explicit night policy")\n'
        '            p.add_argument("--phase", choices=("both", "sync", "analyze"), default="both")\n')
    one('                                 night_scope_path=args.night_scope)\n',
        '                                 night_scope_path=args.night_scope, phase=args.phase)\n')
    one('        if args.command == "run" and result["outcome"] != "COMPLETED_PROVISIONAL":\n            return 1\n',
        '        if args.command == "run" and (\n'
        '            (args.phase == "both" and result["outcome"] != "COMPLETED_PROVISIONAL")\n'
        '            or (args.phase == "analyze" and result["outcome"] == "ANALYSIS_PENDING")):\n'
        '            return 1\n')
    ast.parse(s)
    return s


def shell(*a):
    return subprocess.run(a,capture_output=True,text=True,timeout=20,check=True).stdout.strip()


def stage_write(p: Path, content: str):
    if p.exists() or p.is_symlink():
        raise RuntimeError('TARGET_EXISTS: '+str(p))
    p.parent.mkdir(parents=True,exist_ok=True)
    fd, tmp=tempfile.mkstemp(prefix='.'+p.name+'.',dir=p.parent)
    try:
        with os.fdopen(fd,'w') as f:
            f.write(content);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o600)
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def main():
    if os.geteuid()==0:
        raise RuntimeError('DO_NOT_RUN_AS_ROOT')
    if not SRC.is_dir() or not OLD_DROP.is_file() or OLD_DROP.is_symlink():
        raise RuntimeError('RC13_SOURCE_OR_DROPIN_MISSING')
    old=OLD_DROP.read_text()
    lines=[x.strip().split('=',1)[1] for x in old.splitlines() if x.startswith('ExecStart=') and x.split('=',1)[1].strip()]
    if len(lines)!=1:
        raise RuntimeError('UNEXPECTED_RC13_EXECSTART')
    orig=lines[0]
    if any(x not in orig for x in ('--max-jobs 500','--max-runtime-hours 12', '--night-scope ','rc13-20261030-carryover')):
        raise RuntimeError('RC14_CONFIG_BASELINE_MISMATCH')
    if '--max-segments-per-device' in orig or '--config/home/' in orig:
        raise RuntimeError('PREVIOUS_CONFIG_FIX_REQUIRED')
    active=subprocess.run(['systemctl','--user','is-active','meteormesh-morning.service'],capture_output=True,text=True,timeout=15).stdout.strip()
    if active=='active':
        raise RuntimeError('MORNING_SERVICE_ACTIVE')
    if NEW_DROP.exists() or NEW_DROP.is_symlink() or ANALYSIS.exists() or ANALYSIS.is_symlink() or DST.exists():
        raise RuntimeError('RC14_ALREADY_STAGED_OR_INSTALLED')
    src=SRC/'meteor_morning.py'
    before=src.read_text()
    after=patch(before)
    if '--phase' in orig:
        raise RuntimeError('PHASE_ALREADY_IN_START_COMMAND')
    args=shlex.split(orig)
    if len(args)<8 or '--night-scope' not in args or '--max-jobs' not in args:
        raise RuntimeError('INVALID_RC13_COMMAND')
    if args[0]!='/usr/bin/python3' or args[1]!='-B':
        raise RuntimeError('UNEXPECTED_PYTHON_EXEC')
    args[2]=str(DST/'meteor_morning.py')
    args.extend(['--phase','sync'])
    sync_line=shlex.join(args)
    args[-1]='analyze'
    analyze_line=shlex.join(args)
    # OnSuccess fires also for nonfatal PARTIAL sync. Mount failure returns nonzero, so it doesn't chain.
    drop='[Unit]\nOnSuccess=meteormesh-analyze.service\n\n[Service]\nWorkingDirectory='+str(DST)+'\nExecStart=\nExecStart='+sync_line+'\n'
    analysis='[Unit]\nDescription=MeteorMesh RC1.4 analysis queue after morning sync\nRequiresMountsFor=/srv/meteor-data\nConditionPathIsMountPoint=/srv/meteor-data\n\n[Service]\nType=oneshot\nWorkingDirectory='+str(DST)+'\nExecStart='+analyze_line+'\nTimeoutStartSec=13h\nNice=10\nIOSchedulingClass=best-effort\nIOSchedulingPriority=7\n'
    if '--phase sync' not in drop or '--phase analyze' not in analysis:
        raise RuntimeError('PHASE_RENDER_FAILED')
    # Files are immutable once published. No live service is started or enabled.
    shutil.copytree(SRC,DST,symlinks=True)
    (DST/'meteor_morning.py').write_text(after)
    compile(after,str(DST/'meteor_morning.py'),'exec')
    stage_write(ANALYSIS,analysis)
    stage_write(NEW_DROP,drop)
    try:
        shell('systemctl','--user','daemon-reload')
        shown=shell('systemctl','--user','show','-p','ExecStart','--value','meteormesh-morning.service')
        assert '--phase sync' in shown and str(DST) in shown
        x=shell('systemctl','--user','show','-p','ExecStart','--value','meteormesh-analyze.service')
        assert '--phase analyze' in x and str(DST) in x
        assert shell('systemctl','--user','is-enabled','meteormesh-morning.timer') in ('enabled','static')
    except Exception:
        NEW_DROP.unlink(missing_ok=True)
        ANALYSIS.unlink(missing_ok=True)
        shell('systemctl','--user','daemon-reload')
        raise
    print('RC14_SPLIT: PASS')
    print('MORNING_09:00: INGEST_AND_QUEUE')
    print('AFTER_SYNC: AUTOMATIC_ANALYZE')
    print('MAX_JOBS: 500; RUNTIME: 12H')
    print('CAPTURE_TIMER: UNCHANGED')
    print('MORNING_TIMER: ENABLED_UNCHANGED')
    print('AUTO_DELETE: OFF; VIDEOS_DB: UNTOUCHED')
    print('ROLLBACK: rm',NEW_DROP,ANALYSIS,'&& systemctl --user daemon-reload')

if __name__=='__main__':
    try: main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError, AssertionError) as exc:
        print('DURDU:',exc,file=sys.stderr)
        sys.exit(2)