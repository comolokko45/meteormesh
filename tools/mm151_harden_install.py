#!/usr/bin/env python3
"""RC1.5.1: 19-05 nightly policy and truthful nonfatal post-analysis statuses.

Additive, reversible systemd drop-ins. Does NOT run services, access HDD, delete
footage, change phone recording, database, analysis budgets, or timers.
"""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

HOME = Path.home()
ROOT = HOME / '.local/share/meteormesh/releases/rc15-20261031-consolidated'
HOOKS = ROOT / 'hooks'
POLICY_OLD = HOME / '.local/share/meteormesh/staging/fullnight/meteor-auto-night-policy.py'
POLICY_NEW = HOOKS / 'auto-night-rc151.py'
WRAPPER_OLD = HOOKS / 'after_analysis.py'
WRAPPER_NEW = HOOKS / 'after_analysis_rc151.py'
UNITS = HOME / '.config/systemd/user'
MORNING_DROP = UNITS / 'meteormesh-morning.service.d/zz-rc151-night-policy.conf'
ANALYSIS_DROP = UNITS / 'meteormesh-analyze.service.d/zz-rc151-report.conf'
MANIFEST = ROOT / 'RC151-MANIFEST.json'


def command(*args, check=True):
    p = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if check and p.returncode:
        raise RuntimeError('COMMAND_FAILED: ' + ' '.join(args[:5]) + ': ' + p.stderr[:150])
    return p.stdout.strip()


def one(raw, before, after):
    n = raw.count(before)
    if n != 1:
        raise RuntimeError('UNEXPECTED_PATCH_ANCHOR: ' + repr(before) + ' count=' + str(n))
    return raw.replace(before, after, 1)


def state_ok():
    if os.geteuid() == 0:
        raise RuntimeError('DO_NOT_USE_ROOT')
    if not ROOT.is_dir() or ROOT.is_symlink() or not (ROOT/'MANIFEST.json').is_file():
        raise RuntimeError('RC15_RELEASE_NOT_FOUND')
    for u in ('meteormesh-morning.service', 'meteormesh-analyze.service'):
        if command('systemctl', '--user', 'is-active', u, check=False) == 'active':
            raise RuntimeError('SERVICE_RUNNING: ' + u)
    start = command('systemctl', '--user', 'show', 'meteormesh-morning.service', '-p', 'ExecStart', '--value')
    analysis = command('systemctl', '--user', 'show', 'meteormesh-analyze.service', '-p', 'ExecStart', '--value')
    hook = command('systemctl', '--user', 'show', 'meteormesh-analyze.service', '-p', 'ExecStopPost', '--value')
    pre = command('systemctl', '--user', 'show', 'meteormesh-morning.service', '-p', 'ExecStartPre', '--value')
    if (str(ROOT/'MeteorHub/meteor_morning.py') not in start or '--phase sync' not in start
        or str(ROOT/'MeteorHub/meteor_morning.py') not in analysis or '--phase analyze' not in analysis
        or str(WRAPPER_OLD) not in hook or 'mm01_readonly_preflight.py' not in pre
        or str(POLICY_OLD) not in pre):
        raise RuntimeError('UNEXPECTED_RC15_SERVICE_BASELINE')
    timeout = command('systemctl', '--user', 'show', 'meteormesh-analyze.service', '-p', 'TimeoutStopUSec', '--value')
    if timeout != '20min':
        raise RuntimeError('RC15_20MIN_TIMEOUT_NOT_FOUND')


def build_policy(raw):
    raw = one(raw, 'dt.time(7,0)', 'dt.time(5,0)')
    raw = one(raw, "'local_night_window':'19:00-07:00 Europe/Istanbul'",
              "'local_night_window':'19:00-05:00 Europe/Istanbul'")
    raw = one(raw, '.hour==7', '.hour==5')
    raw = one(raw, '12*3600*1000', '10*3600*1000')
    raw = raw.replace('previous 19:00--07:00 session', 'previous 19:00--05:00 session')
    ast.parse(raw, filename='auto-night-rc151.py')
    return raw


WRAPPER = r'''#!/usr/bin/env python3
"""RC1.5.1: three existing post-analysis observers, warnings kept in journal."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys

BASE = Path(__file__).resolve().parent
STEPS = [('NIGHT_SNAPSHOT','meteor-night-snapshot.py',[]),
         ('EVENT_REVIEW','meteor-event-review.py',[]),
         ('AUTO_PACKAGE','meteor-auto-package.py',['--limit','10'])]


def classify(label, ret, lines):
    if ret != 0:
        return 'ERROR_EXIT_'+str(ret)
    error_prefix = {'NIGHT_SNAPSHOT':'NIGHT_ANALYSIS_SNAPSHOT_UNAVAILABLE:',
                    'EVENT_REVIEW':'EVENT_REVIEW_UNAVAILABLE:',
                    'AUTO_PACKAGE':'EVENT_AUTO_PACKAGE_UNAVAILABLE:'}
    if any(s.startswith(error_prefix[label]) for s in lines):
        return 'ERROR_REPORTED'
    success_prefix = {'NIGHT_SNAPSHOT':'NIGHT_ANALYSIS_SNAPSHOT:',
                      'EVENT_REVIEW':'EVENT_REVIEW_INDEX:',
                      'AUTO_PACKAGE':'EVENT_AUTO_PACKAGE:'}
    matches = [s.split(': ',1)[1] for s in lines if s.startswith(success_prefix[label])]
    if len(matches) != 1:
        return 'ERROR_MISSING_RESULT'
    try:
        doc = json.loads(matches[0])
        if not isinstance(doc, dict):
            return 'ERROR_BAD_RESULT'
        if label == 'NIGHT_SNAPSHOT':
            if doc.get('schema') != 'meteormesh.night-analysis-snapshot.v1':
                return 'ERROR_BAD_SCHEMA'
            return 'WARNING_PARTIAL' if doc.get('status') != 'PROVISIONAL_ANALYZED' else 'OK'
        if label == 'EVENT_REVIEW':
            if type(doc.get('failed_sources')) is not int:
                return 'ERROR_BAD_SCHEMA'
            return 'WARNING_SOURCE_ERRORS' if doc['failed_sources'] else 'OK'
        if label == 'AUTO_PACKAGE':
            if type(doc.get('invalid')) is not int:
                return 'ERROR_BAD_SCHEMA'
            return 'WARNING_INVALID_RECEIPTS' if doc['invalid'] else 'OK'
    except (ValueError, KeyError, TypeError):
        return 'ERROR_BAD_RESULT'
    return 'ERROR_UNEXPECTED_STEP'


def main():
    results = []
    for label, name, args in STEPS:
        try:
            p = subprocess.run(['/usr/bin/python3','-B',str(BASE/name),*args],
                               capture_output=True,text=True,timeout=300)
            lines = p.stdout.splitlines()
            for line in lines:
                print(line, flush=True)
            if p.stderr:
                print('RC151_HOOK_STDERR: '+label+' '+p.stderr[:240].replace('\n',' '),flush=True)
            status = classify(label, p.returncode, lines)
        except (OSError, subprocess.TimeoutExpired) as e:
            status = 'ERROR_'+type(e).__name__
        results.append(label+'='+status)
    print('RC151_AFTER_ANALYSIS: '+','.join(results),flush=True)
    return 0  # Never masks original detector/service result.

if __name__ == '__main__':
    sys.exit(main())
'''


def read_commands():
    # Extract the exact original two pre-start commands; keep the disk guard unchanged.
    root = UNITS / 'meteormesh-morning.service.d'
    found = []
    for p in sorted(root.glob('*.conf')):
        if p.is_symlink():
            raise RuntimeError('SYMLINK_SERVICE_DROPIN')
        in_service = False
        for line in p.read_text().splitlines():
            s = line.strip()
            if s.startswith('['):
                in_service = (s == '[Service]')
            if in_service and s.startswith('ExecStartPre=') and s[len('ExecStartPre='):]:
                found.append(s[len('ExecStartPre='):])
    pre = [v for v in found if 'mm01_readonly_preflight.py' in v]
    policy = [v for v in found if str(POLICY_OLD) in v]
    if len(pre)!=1 or len(policy)!=1 or len(found)!=2:
        raise RuntimeError('UNEXPECTED_PRESTART_CONFIGURATION')
    if not pre[0].startswith('/usr/bin/python3 -B ') or not policy[0].startswith('/usr/bin/python3 -B '):
        raise RuntimeError('BAD_PRESTART_COMMAND')
    if not policy[0].endswith(' --write'):
        raise RuntimeError('OLD_POLICY_COMMAND_UNEXPECTED')
    return pre[0]


def save_new(p, payload, mode=0o600):
    if p.exists() or p.is_symlink():
        raise RuntimeError('DESTINATION_EXISTS: '+str(p))
    p.parent.mkdir(parents=True, exist_ok=True)
    fd,tmp = tempfile.mkstemp(prefix='.'+p.name+'.',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(payload);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,mode)
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def main():
    state_ok()
    for p in (POLICY_OLD, WRAPPER_OLD):
        if p.is_symlink() or not p.is_file():
            raise RuntimeError('SOURCE_MISSING_OR_SYMLINK: '+str(p))
    oldpolicy=POLICY_OLD.read_text()
    # The currently installed helper must be the known RC1.3 generator.
    if "GENERATOR = 'meteormesh.daily-night.v1'" not in oldpolicy:
        raise RuntimeError('UNRECOGNIZED_AUTO_NIGHT_GENERATOR')
    policy=build_policy(oldpolicy).encode()
    ast.parse(WRAPPER,filename='after_analysis_rc151.py')
    preflight=read_commands()
    unit_m = ('[Service]\nExecStartPre=\nExecStartPre='+preflight+'\n'
              'ExecStartPre=/usr/bin/python3 -B '+str(POLICY_NEW)+' --write\n')
    unit_a = ('[Service]\nExecStopPost=\nExecStopPost=/usr/bin/python3 -B '
              +str(WRAPPER_NEW)+'\n')
    manifest={'schema':'meteormesh.rc151.hardening.v1',
              'policy_sha256':hashlib.sha256(policy).hexdigest(),
              'wrapper_sha256':hashlib.sha256(WRAPPER.encode()).hexdigest(),
              'night_window':'19:00-05:00 Europe/Istanbul',
              'no_data_migration':True,'no_auto_delete':True}
    files=[(POLICY_NEW,policy,0o600),
           (WRAPPER_NEW,WRAPPER.encode(),0o600),
           (MORNING_DROP,unit_m.encode(),0o644),
           (ANALYSIS_DROP,unit_a.encode(),0o644),
           (MANIFEST,(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode(),0o600)]
    created=[]
    try:
        for path,contents,mode in files:
            save_new(path,contents,mode)
            created.append(path)
        command('systemctl','--user','daemon-reload')
        m=command('systemctl','--user','show','meteormesh-morning.service','-p','ExecStartPre','--value')
        a=command('systemctl','--user','show','meteormesh-analyze.service','-p','ExecStopPost','--value')
        if (m.count(str(POLICY_NEW))!=1 or str(POLICY_OLD) in m
            or m.count('mm01_readonly_preflight.py')!=1
            or a.count(str(WRAPPER_NEW))!=1 or str(WRAPPER_OLD) in a):
            raise RuntimeError('SYSTEMD_EFFECTIVE_SETTINGS_MISMATCH')
        if command('systemctl','--user','show','meteormesh-analyze.service',
                   '-p','TimeoutStopUSec','--value')!='20min':
            raise RuntimeError('TIMEOUT_OVERRIDE_LOST')
        for p,_,_ in files[:2]:
            ast.parse(p.read_text(),filename=p.name)
        state_ok_after=True
    except BaseException:
        for p in reversed(created):p.unlink(missing_ok=True)
        subprocess.run(['systemctl','--user','daemon-reload'],capture_output=True,timeout=15)
        raise
    print('RC151_POLICY_AND_REPORT: PASS')
    print('NIGHT_POLICY: 19:00-05:00 FROM_NEXT_MORNING')
    print('AFTER_ANALYSIS: OK_WARNING_ERROR_VISIBLE')
    print('TIMEOUT_STOP: 20min; ANALYSIS_BUDGET: UNCHANGED')
    print('VIDEOS_SQLITE_TIMERS_PHONES: UNCHANGED')
    print('ROLLBACK: rm '+str(MORNING_DROP)+' '+str(ANALYSIS_DROP)+' && systemctl --user daemon-reload')

if __name__ == '__main__':
    try:main()
    except (OSError,RuntimeError,ValueError,SyntaxError,subprocess.SubprocessError) as e:
        print('RC151_STOPPED: '+type(e).__name__+': '+str(e)[:230],file=sys.stderr)
        sys.exit(2)
