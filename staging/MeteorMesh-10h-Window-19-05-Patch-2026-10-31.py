#!/usr/bin/env python3
"""Adjust live MeteorMesh capture and morning scope from 19-07 to 19-05.

Preserves active timers, media, databases, device settings and tokens. Creates
private backups, validates both scripts before publishing and patches current
scope to avoid the same-night immutable-policy mismatch at the 09:00 run.
"""
from __future__ import annotations
import ast
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

HOME = Path.home()
STAGE = HOME / '.local/share/meteormesh/staging/fullnight'
CAP = HOME / '.local/lib/meteormesh/meteor-capture-loop.py'
GEN = STAGE / 'meteor-auto-night-policy.py'
POL = STAGE / 'night-scope-current.json'
TZ = ZoneInfo('Europe/Istanbul')

def stop(message):
    raise RuntimeError(message)

def check_state():
    for u,verb,expected in (
        ('meteormesh-capture.timer','is-enabled','enabled'),
        ('meteormesh-capture.timer','is-active','active'),
        ('meteormesh-morning.timer','is-enabled','enabled'),
        ('meteormesh-morning.timer','is-active','active'),
        ('meteormesh-capture.service','is-active','inactive'),
        ('meteormesh-morning.service','is-active','inactive'),
    ):
        p = subprocess.run(['systemctl','--user',verb,u],text=True,capture_output=True,timeout=12)
        if p.stdout.strip()!=expected:
            stop(f'{u} {verb}: expected {expected}, got {p.stdout.strip()}')

def validated_file(path):
    if not path.is_file() or path.is_symlink() or (path.stat().st_mode & 0o022):
        stop('Missing, symlinked or writable-by-others file: '+str(path))
    return path.read_bytes()

def apply_edits(path, replacements):
    old=validated_file(path)
    s=old.decode('utf-8')
    for before,after in replacements:
        if before in s:
            if s.count(before)!=1:stop(f'Ambiguous source pattern in {path.name}: {before}')
            s=s.replace(before,after,1)
        elif after not in s:
            stop(f'Unknown source version in {path.name}: {before}')
    ast.parse(s,filename=str(path))
    return old,s.encode('utf-8')

def atomic_bytes(path,data):
    mode=path.stat().st_mode & 0o777
    fd,tmp=tempfile.mkstemp(prefix='.mm10-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as out:
            os.fchmod(out.fileno(),mode)
            out.write(data)
            out.flush();os.fsync(out.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.lexists(tmp):os.unlink(tmp)

def policy_change():
    if not POL.exists(): return None,None
    src=validated_file(POL)
    doc=json.loads(src)
    old=doc.get('local_night_window')
    if old=='19:00-05:00 Europe/Istanbul':
        return src,src
    if old!='19:00-07:00 Europe/Istanbul' or doc.get('status')!='ARMED':
        stop('Unexpected current night-scope status/window; no changes')
    if doc.get('schema')!='meteormesh.night_scope.v1' or doc.get('retention_auto_delete') is not False:
        stop('Unexpected current night-scope schema/retention')
    excluded=doc.get('excluded_segments',{})
    if {k:len(v) for k,v in excluded.items()}!={'cam-viva':10,'cam-ruby':2,'cam-surya':2}:
        stop('Old exclusion baseline changed')
    end=datetime.fromtimestamp(doc['end_utc_ms']/1000,timezone.utc).astimezone(TZ)
    if (end.hour,end.minute,end.second)!=(7,0,0):
        stop('Unexpected current policy end time')
    new_end=end.replace(hour=5)
    if doc['start_utc_ms']>=int(new_end.timestamp()*1000):
        stop('10-hour end time precedes scope start')
    doc['end_utc_ms']=int(new_end.timestamp()*1000)
    doc['local_night_window']='19:00-05:00 Europe/Istanbul'
    new=(json.dumps(doc,ensure_ascii=False,indent=2)+'\n').encode()
    return src,new

def main():
    if os.geteuid()==0:stop('Run as meteor user, not root')
    check_state()
    cap_old,cap_new=apply_edits(CAP,[
        ('return at.hour >= 19 or at.hour < 7','return at.hour >= 19 or at.hour < 5'),
        ("'NIGHT_19_07' if night else 'DAY_07_19'", "'NIGHT_19_05' if night else 'DAY_05_19'"),
        ('(6,True),(7,False)', '(4,True),(5,False),(6,False),(7,False)'),
        ('CAPTURE_SELFTEST: PASS (6 window cases', 'CAPTURE_SELFTEST: PASS (8 window cases'),
    ])
    gen_old,gen_new=apply_edits(GEN,[
        ('dt.time(7,0)','dt.time(5,0)'),
        ("'19:00-07:00 Europe/Istanbul'", "'19:00-05:00 Europe/Istanbul'"),
        ('astimezone(TZ).hour==7','astimezone(TZ).hour==5'),
        ("==12*3600*1000", "==10*3600*1000"),
    ])
    pol_old,pol_new=policy_change()
    changes=[(CAP,cap_old,cap_new),(GEN,gen_old,gen_new)]
    if pol_old is not None:changes.append((POL,pol_old,pol_new))
    if all(a==b for _,a,b in changes):
        print('TEN_HOUR_WINDOW: ALREADY_APPLIED')
        return
    # Private backup created before modifying anything.
    bk=STAGE / ('rollback-10h-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    bk.mkdir(mode=0o700)
    for i,(path,old,new) in enumerate(changes):
        saved=bk/(str(i)+'-'+path.name)
        fd=os.open(saved,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as out:out.write(old)
    updated=[]
    try:
        for path,old,new in changes:
            if old!=new:
                atomic_bytes(path,new)
                updated.append((path,old))
        for path,old,new in changes:
            if path.read_bytes()!=new:
                stop('Post-write mismatch: '+str(path))
        for path,label in ((CAP,'CAPTURE'),(GEN,'SCOPE')):
            arg='--self-test' if path==CAP else '--test'
            p=subprocess.run(['/usr/bin/python3','-B',str(path),arg],capture_output=True,text=True,timeout=30)
            if p.returncode or 'PASS' not in p.stdout:
                stop(label+' regression: '+(p.stderr+p.stdout)[-350:])
        check_state()
    except Exception:
        for path,old in reversed(updated):
            atomic_bytes(path,old)
        raise
    print('CAPTURE: 19:00-05:00 / 10H')
    print('MORNING_SCOPE: 19:00-05:00')
    print('CAPTURE_AND_SCOPE_TESTS: PASS')
    print('TIMERS: ENABLED_UNCHANGED')
    print('BACKUP:',bk)
    print('MEDIA_DB_PHONE: UNTOUCHED; AUTO_DELETE: OFF')
    print('TEN_HOUR_WINDOW: PASS')

if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired) as err:
        print('STOP:',err,file=sys.stderr)
        sys.exit(2)
