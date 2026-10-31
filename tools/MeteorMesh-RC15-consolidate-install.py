#!/usr/bin/env python3
"""MeteorMesh RC1.5: reversible consolidation of the proven RC1.4 runtime.

No cameras, disk data, SQLite, network, timers or running service are touched.
The exact installed RC1.4 Hub is copied, not rebuilt from an outdated zip.
Two 99- overrides replace scattered runtime commands and post-analysis hooks.
No test clips, uploads, archives or deletes are triggered during installation.
"""
from __future__ import annotations
import ast
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

HOME = Path.home()
RELEASE = HOME/'.local/share/meteormesh/releases'
OLD = RELEASE/'rc14-20261031-split/MeteorHub'
NEW_ROOT = RELEASE/'rc15-20261031-consolidated'
NEW = NEW_ROOT/'MeteorHub'
HOOKS = NEW_ROOT/'hooks'
UNITS = HOME/'.config/systemd/user'
MORNING_OLD = UNITS/'meteormesh-morning.service.d/97-meteormesh-rc14-split.conf'
MORNING_NEW = UNITS/'meteormesh-morning.service.d/99-meteormesh-rc15.conf'
ANALYSIS_OLD = UNITS/'meteormesh-analyze.service'
ANALYSIS_NEW = UNITS/'meteormesh-analyze.service.d/99-meteormesh-rc15.conf'
LEGACY_HOOKS = HOME/'.local/lib/meteormesh'
SOURCE_NAMES = {
    'meteor-night-snapshot.py':'night_snapshot',
    'meteor-event-review.py':'event_review',
    'meteor-auto-package.py':'auto_package',
    'meteor-bundle-once.py':'bundle_once',
}
IDENT = 'METEORMESH_RC15_CONSOLIDATION_V1'


def call(*args: str, check: bool = True) -> str:
    p=subprocess.run(args,capture_output=True,text=True,timeout=20)
    if check and p.returncode:
        raise RuntimeError('COMMAND_FAILED: '+' '.join(args[:4])+' '+p.stderr.strip()[:150])
    return p.stdout.strip()


def only_exec(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('MISSING_OR_SYMLINK_UNIT: '+str(path))
    lines=[l.partition('=')[2].strip() for l in path.read_text().splitlines()
           if l.startswith('ExecStart=') and l.partition('=')[2].strip()]
    if len(lines)!=1:
        raise RuntimeError('UNEXPECTED_EXECSTART_COUNT: '+path.name)
    return lines[0]


def assert_service_state():
    for unit in ('meteormesh-morning.service','meteormesh-analyze.service'):
        if call('systemctl','--user','is-active',unit,check=False)=='active':
            raise RuntimeError('SERVICE_CURRENTLY_RUNNING: '+unit)
    if call('systemctl','--user','is-enabled','meteormesh-morning.timer')!='enabled':
        raise RuntimeError('MORNING_TIMER_NOT_ENABLED')
    if call('systemctl','--user','is-active','meteormesh-morning.timer')!='active':
        raise RuntimeError('MORNING_TIMER_NOT_ACTIVE')


def verify_layout() -> tuple[str,str]:
    if os.geteuid()==0:
        raise RuntimeError('DO_NOT_RUN_AS_ROOT')
    assert_service_state()
    if NEW_ROOT.exists() or NEW_ROOT.is_symlink() or MORNING_NEW.exists() or MORNING_NEW.is_symlink() or ANALYSIS_NEW.exists() or ANALYSIS_NEW.is_symlink():
        raise RuntimeError('RC15_TARGET_ALREADY_EXISTS_USE_STATUS_OR_ROLLBACK')
    if OLD.is_symlink() or not OLD.is_dir() or not (OLD/'storage_guard.py').is_file() or not (OLD/'night_scope.py').is_file():
        raise RuntimeError('APPROVED_RC14_HUB_MISSING')
    for root,dirs,files in os.walk(OLD,followlinks=False):
        for n in dirs+files:
            if (Path(root)/n).is_symlink():
                raise RuntimeError('RC14_SOURCE_CONTAINS_SYMLINK: '+n)
    source=(OLD/'meteor_morning.py').read_text(encoding='utf-8')
    for marker in ('phase != "analyze"', 'phase != "sync"', 'choices=("both", "sync", "analyze")'):
        if marker not in source:
            raise RuntimeError('RC14_SPLIT_CODE_MISSING: '+marker)
    m=only_exec(MORNING_OLD)
    a=only_exec(ANALYSIS_OLD)
    for label,cmd,phase in (('morning',m,'sync'),('analysis',a,'analyze')):
        required=('rc14-20261031-split/MeteorHub/meteor_morning.py',
                  '--phase '+phase,'--night-scope ','--max-jobs 500',
                  '--max-runtime-hours 12', '--data-dir /srv/meteor-data')
        if any(x not in cmd for x in required):
            raise RuntimeError('BASELINE_MISMATCH_'+label.upper())
        parts=shlex.split(cmd)
        if parts[:2]!=['/usr/bin/python3','-B'] or parts.count('--phase')!=1:
            raise RuntimeError('BAD_BASELINE_COMMAND_'+label.upper())
    pre=call('systemctl','--user','show','meteormesh-morning.service','-p','ExecStartPre','--value')
    if not all(x in pre for x in ('mm01_readonly_preflight.py','meteor-auto-night-policy.py')):
        raise RuntimeError('MISSING_EXISTING_DISK_OR_NIGHT_GUARD')
    for name in SOURCE_NAMES:
        p=LEGACY_HOOKS/name
        if p.is_symlink() or not p.is_file():
            raise RuntimeError('INSTALLED_HOOK_MISSING: '+name)
        ast.parse(p.read_text(encoding='utf-8'),filename=name)
    current=call('systemctl','--user','show','meteormesh-analyze.service','-p','ExecStopPost','--value')
    if any(n not in current for n in ('meteor-night-snapshot.py','meteor-event-review.py','meteor-auto-package.py')):
        raise RuntimeError('EXPECTED_THREE_ANALYSIS_HOOKS_MISSING')
    return m,a


def patch_path_once(s: str, before: str, after: str, name: str) -> str:
    if s.count(before)!=1:
        raise RuntimeError('HOOK_SOURCE_MISMATCH_'+name)
    return s.replace(before,after,1)


def load_hook(name: str) -> bytes:
    text=(LEGACY_HOOKS/name).read_text()
    old='rc14-20261031-split/MeteorHub'
    new='rc15-20261031-consolidated/MeteorHub'
    if name in ('meteor-event-review.py','meteor-auto-package.py','meteor-bundle-once.py'):
        text=patch_path_once(text,old,new,name+'_HUB')
    if name=='meteor-auto-package.py':
        text=patch_path_once(text,
            "BUNDLER = Path.home() / '.local/lib/meteormesh/meteor-bundle-once.py'",
            "BUNDLER = Path(__file__).with_name('meteor-bundle-once.py')",'BUNDLER')
    ast.parse(text,filename=name)
    return text.encode('utf-8')


WRAPPER = '''#!/usr/bin/env python3
"""RC1.5 single post-analysis sequencer. Independent nonfatal journal observers."""
from __future__ import annotations
from pathlib import Path
import subprocess
import sys

BASE = Path(__file__).resolve().parent
STEPS = [('NIGHT_SNAPSHOT','meteor-night-snapshot.py',[]),
         ('EVENT_REVIEW','meteor-event-review.py',[]),
         ('AUTO_PACKAGE','meteor-auto-package.py',['--limit','10'])]

def main():
    results=[]
    for label,name,args in STEPS:
        try:
            p=subprocess.run(['/usr/bin/python3','-B',str(BASE/name),*args],
                 capture_output=True,text=True,timeout=300)
            for line in p.stdout.splitlines():print(line,flush=True)
            if p.stderr:print('RC15_HOOK_STDERR: '+label+' '+p.stderr[:240].replace('\\n',' '),flush=True)
            status='OK' if p.returncode==0 else 'ERROR_'+str(p.returncode)
        except (OSError,subprocess.TimeoutExpired) as e:
            status=type(e).__name__
        results.append(label+'='+status)
    print('RC15_AFTER_ANALYSIS: '+','.join(results),flush=True)
    return 0   # Never masks detector exit status

if __name__=='__main__':sys.exit(main())
'''


def atomic_new(path: Path, data: bytes, mode: int) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError('DESTINATION_EXISTS: '+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp,mode)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def rewrite_start(command: str) -> str:
    args=shlex.split(command)
    old_script=str(OLD/'meteor_morning.py')
    if args.count(old_script)!=1:
        raise RuntimeError('SOURCE_SCRIPT_NOT_FOUND_ONCE')
    args[args.index(old_script)]=str(NEW/'meteor_morning.py')
    return shlex.join(args)


def stage(morning: str,analysis: str):
    NEW_ROOT.mkdir(parents=True,mode=0o700)
    shutil.copytree(OLD,NEW,symlinks=False,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    HOOKS.mkdir(mode=0o700)
    checksums={}
    for name in SOURCE_NAMES:
        data=load_hook(name)
        atomic_new(HOOKS/name,data,0o600)
        checksums['hooks/'+name]=hashlib.sha256(data).hexdigest()
    ast.parse(WRAPPER)
    atomic_new(HOOKS/'after_analysis.py',WRAPPER.encode('utf-8'),0o600)
    checksums['hooks/after_analysis.py']=hashlib.sha256(WRAPPER.encode()).hexdigest()
    for p in NEW.rglob('*.py'):
        ast.parse(p.read_text(encoding='utf-8'),filename=str(p))
        checksums[str(p.relative_to(NEW_ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    stamp=dt.datetime.now(dt.timezone.utc).isoformat()
    manifest={'schema':'meteormesh.rc15.consolidated.v1','created_utc':stamp,
              'base':'rc14-20261031-split','entrypoints':['sync','analyze'],
              'hooks':['night_snapshot','event_review','auto_package'],
              'auto_delete':False,'cloud_upload':False,'test_clips_generated_automatically':False,
              'files_sha256':checksums}
    atomic_new(NEW_ROOT/'MANIFEST.json',
               (json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode(),0o600)
    new_m=rewrite_start(morning)
    new_a=rewrite_start(analysis)
    unit_m='[Service]\nExecStart=\nExecStart='+new_m+'\n'
    unit_a=('[Service]\nExecStart=\nExecStart='+new_a+'\n'
            'ExecStopPost=\nExecStopPost=/usr/bin/python3 -B '+str(HOOKS/'after_analysis.py')+'\n')
    atomic_new(MORNING_NEW,unit_m.encode(),0o644)
    atomic_new(ANALYSIS_NEW,unit_a.encode(),0o644)


def verify_live():
    call('systemctl','--user','daemon-reload')
    morning=call('systemctl','--user','show','meteormesh-morning.service','-p','ExecStart','--value')
    analysis=call('systemctl','--user','show','meteormesh-analyze.service','-p','ExecStart','--value')
    after=call('systemctl','--user','show','meteormesh-analyze.service','-p','ExecStopPost','--value')
    pre=call('systemctl','--user','show','meteormesh-morning.service','-p','ExecStartPre','--value')
    if str(NEW/'meteor_morning.py') not in morning or '--phase sync' not in morning:
        raise RuntimeError('NEW_SYNC_UNIT_NOT_EFFECTIVE')
    if str(NEW/'meteor_morning.py') not in analysis or '--phase analyze' not in analysis:
        raise RuntimeError('NEW_ANALYZE_UNIT_NOT_EFFECTIVE')
    if after.count('after_analysis.py')!=1 or any(x in after for x in ('meteor-night-snapshot.py','meteor-event-review.py','meteor-auto-package.py')):
        raise RuntimeError('NOT_A_SINGLE_HOOK')
    if not all(x in pre for x in ('mm01_readonly_preflight.py','meteor-auto-night-policy.py')):
        raise RuntimeError('MM01_PREFLIGHT_OR_SCOPE_LOST')
    assert_service_state()


def main():
    before_m,before_a=verify_layout()
    try:
        stage(before_m,before_a)
        verify_live()
    except BaseException:
        for drop in (ANALYSIS_NEW,MORNING_NEW):drop.unlink(missing_ok=True)
        subprocess.run(['systemctl','--user','daemon-reload'],capture_output=True,timeout=15)
        # This release was freshly generated by this process. Production data and old release are separate.
        if NEW_ROOT.is_dir() and not NEW_ROOT.is_symlink():
            shutil.rmtree(NEW_ROOT)
        raise
    print('RC15_CONSOLIDATE: PASS')
    print('HUB_RELEASE: '+str(NEW))
    print('MORNING_09:00: SYNC_UNCHANGED')
    print('ANALYSIS: SEPARATE_SINGLE_WORKER_UNCHANGED')
    print('AFTER_ANALYSIS: ONE_CHAIN_SNAPSHOT_REVIEW_PACKAGES')
    print('PHONE_MONITOR_AND_AGENT: UNCHANGED')
    print('DISK_GUARD_AND_NIGHT_POLICY: PRESERVED')
    print('TIMERS_AND_VIDEOS_SQLITE: UNTOUCHED')
    print('AUTO_DELETE_AND_CLOUD: OFF')
    print('ROLLBACK: rm '+str(MORNING_NEW)+' '+str(ANALYSIS_NEW)+' && systemctl --user daemon-reload')


if __name__=='__main__':
    try: main()
    except (RuntimeError,OSError,ValueError,subprocess.SubprocessError,SyntaxError) as e:
        print('RC15_STOPPED: '+type(e).__name__+': '+str(e)[:220],file=sys.stderr)
        sys.exit(2)
