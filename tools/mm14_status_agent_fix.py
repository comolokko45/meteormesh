#!/usr/bin/env python3
"""MeteorMesh RC1.4 status-agent compatibility, atomic and rollbackable.

Requires approved RC1.4 morning split, never starts/stops timers or touches data.
"""
from __future__ import annotations
import ast
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

H=Path.home()
ROOT=H/'.local/share/meteormesh/releases'
SOURCE=ROOT/'rc12-20261030/MeteorAgent-1.1'
DEST=ROOT/'rc14-20261031-status/MeteorAgent-1.2'
WRAPPER=H/'.local/lib/meteormesh/meteor-status-agent.py'
DROP=H/'.config/systemd/user/meteormesh-status.service.d/40-rc14-warning-exit.conf'
DROP_CONTENT='[Service]\nSuccessExitStatus=1\n'
OLD_REF='rc12-20261030/MeteorAgent-1.1/meteor_agent_cli.py'
NEW_REF='rc14-20261031-status/MeteorAgent-1.2/meteor_agent_cli.py'


def run(*args):
    p=subprocess.run(args,text=True,capture_output=True,timeout=15,check=True)
    return p.stdout.strip()


def replace_once(text,before,after,label):
    if text.count(before)!=1: raise RuntimeError('SOURCE_MISMATCH_'+label)
    return text.replace(before,after,1)


def save_new(path,text,mode=0o600):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp,mode)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def main():
    if os.geteuid()==0: raise RuntimeError('NO_ROOT')
    if not SOURCE.is_dir() or not WRAPPER.is_file() or WRAPPER.is_symlink():
        raise RuntimeError('AGENT_SOURCE_OR_WRAPPER_MISSING')
    if DEST.exists() or DROP.exists() or DROP.is_symlink():
        raise RuntimeError('ALREADY_INSTALLED_OR_CONFLICT')
    if run('systemctl','--user','is-enabled','meteormesh-morning.timer')!='enabled':
        raise RuntimeError('MORNING_TIMER_NOT_ENABLED')
    if run('systemctl','--user','is-active','meteormesh-morning.timer')!='active':
        raise RuntimeError('MORNING_TIMER_NOT_ACTIVE')
    cmd=run('systemctl','--user','show','meteormesh-morning.service','-p','ExecStart','--value')
    analysis_cmd=run('systemctl','--user','show','meteormesh-analyze.service','-p','ExecStart','--value')
    if not all(x in cmd for x in ('rc14-20261031-split','--phase sync')) or not all(x in analysis_cmd for x in ('rc14-20261031-split','--phase analyze')):
        raise RuntimeError('RC14_SPLIT_NOT_VERIFIED')
    if run('systemctl','--user','is-enabled','meteormesh-status.timer')!='enabled' or run('systemctl','--user','is-active','meteormesh-status.timer')!='active':
        raise RuntimeError('STATUS_TIMER_NOT_ACTIVE')
    if OLD_REF not in WRAPPER.read_text():
        raise RuntimeError('OLD_WRAPPER_NOT_FOUND')

    engine=(SOURCE/'agent_engine.py').read_text()
    timer_old='''        if not isinstance(timer, Mapping):
            found.add("TIMER_STATE_UNKNOWN")
        elif (timer.get("enabled") in ("enabled", "enabled-runtime")
              or timer.get("active") == "active"):
            found.add("MORNING_ENABLED")
        elif timer.get("enabled") not in ("disabled", "masked") or timer.get("active") != "inactive":
            found.add("TIMER_STATE_UNKNOWN")'''
    timer_new='''        if not isinstance(timer, Mapping):
            found.add("TIMER_STATE_UNKNOWN")
        elif (timer.get("enabled") not in ("enabled", "enabled-runtime")
              or timer.get("active") != "active"):
            found.add("TIMER_STATE_UNKNOWN")'''
    engine=replace_once(engine,timer_old,timer_new,'TIMER_LOGIC')
    engine=replace_once(engine,'"MORNING_ENABLED": ("CRITICAL", "Henüz onaylanmayan sabah zamanlayıcısı etkin görünüyor.", "Zamanlayıcıyı operatör kontrol etsin; Agent servis değiştirmez."),\n    ','','CAUSE')
    engine=replace_once(engine,'"MORNING_ENABLED", ','','PRIORITY')
    engine=replace_once(engine,'"Sabah zamanlayıcısının kapalı olduğu doğrulanamadı.", "Zamanlayıcı durumunu salt okunur yöntemle kontrol et; Agent etkinleştirmez veya kapatmaz."','"09:00 zamanlayıcısının etkin olduğu doğrulanamadı.", "RC1.4 sabah zamanlayıcısını operatör kontrol etsin; Agent servis değiştirmez."','NOTICE')
    ast.parse(engine)

    review=(SOURCE/'agent_review.py').read_text()
    review=replace_once(review,'elif "MORNING_ENABLED" in causes or "TIMER_STATE_UNKNOWN" in causes:', 'elif "TIMER_STATE_UNKNOWN" in causes:', 'REVIEW_TIMER')
    review=replace_once(review,'"BLOCKED", "MORNING_TIMER_NOT_SAFELY_DISABLED",\n            "09:00 zamanlayıcısının kapalı olduğunu operatör doğrulamalı."','"BLOCKED", "MORNING_TIMER_UNEXPECTED",\n            "09:00 zamanlayıcısının etkin olduğunu operatör doğrulamalı."','REVIEW_MESSAGE')
    review=replace_once(review,'"scheduled_morning_ingest": "NOT_AUTHORIZED",','"scheduled_morning_ingest": "RC14_CONFIGURED",','REVIEW_APPROVAL')
    ast.parse(review)

    wrapper_old=WRAPPER.read_text()
    wrapper_new=replace_once(wrapper_old,OLD_REF,NEW_REF,'WRAPPER_REF')
    ast.parse(wrapper_new)
    backup=WRAPPER.with_name(WRAPPER.name+'.bak-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    if backup.exists(): raise RuntimeError('BACKUP_CONFLICT')

    destroot=DEST.parent
    if destroot.exists():raise RuntimeError('DESTROOT_EXISTS')
    destroot.mkdir(parents=True)
    installed=False
    drop_created=False
    try:
        shutil.copytree(SOURCE,DEST)
        (DEST/'agent_engine.py').write_text(engine)
        (DEST/'agent_review.py').write_text(review)
        importlib.invalidate_caches()
        spec=importlib.util.spec_from_file_location('meteor_engine_compat',DEST/'agent_engine.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        snapshot={
          'schema':'meteormesh.local-status.v1','checked_tr':datetime.now(timezone.utc).isoformat(),
          'read_only':True,'runs_detector':False,'can_delete':False,
          'hdd':{'path':'/srv/meteor-data','is_mount':True,'fstype':'ext4',
                 'uuid':module.EXPECTED_UUID,'mount_verification':'VERIFIED','free_gib':250},
          'morning_timer':{'enabled':'enabled','active':'active'},
          'database':{'state':'NOT_CREATED'},'issues':[],'status':'OK'}
        ok=module.evaluate(snapshot)
        if ok['level']!='OBSERVED_OK' or ok['causes']:
            raise RuntimeError('AGENT_EXPECTED_TIMER_TEST_FAILED')
        snapshot['morning_timer']['active']='inactive'
        bad=module.evaluate(snapshot)
        if 'TIMER_STATE_UNKNOWN' not in {x['code'] for x in bad['causes']}:
            raise RuntimeError('AGENT_DISCONNECTED_TIMER_TEST_FAILED')
        shutil.copy2(WRAPPER,backup)
        save_new(WRAPPER,wrapper_new,WRAPPER.stat().st_mode & 0o777)
        installed=True
        save_new(DROP,DROP_CONTENT,0o644)
        drop_created=True
        run('systemctl','--user','daemon-reload')
        success=run('systemctl','--user','show','meteormesh-status.service','-p','SuccessExitStatus','--value')
        if '1' not in success.split():
            raise RuntimeError('SUCCESS_EXIT_STATUS_NOT_EFFECTIVE: '+success)
        agent_cmd=run('systemctl','--user','show','meteormesh-status.service','-p','ExecStart','--value')
        if str(WRAPPER) not in agent_cmd:
            raise RuntimeError('STATUS_AGENT_WRAPPER_NOT_ACTIVE')
    except Exception:
        if installed: shutil.copy2(backup,WRAPPER)
        if drop_created: DROP.unlink(missing_ok=True)
        subprocess.run(['systemctl','--user','daemon-reload'],capture_output=True)
        shutil.rmtree(destroot,ignore_errors=True)
        raise
    print('RC14_STATUS_AGENT: PASS')
    print('MORNING_09:00: APPROVED_NO_FALSE_CRITICAL')
    print('PARTIAL_NIGHT: WARNING_PRESERVED')
    print('STATUS_EXIT_1: WARNING_ACCEPTED_NOT_FAILED')
    print('STATUS_EXIT_2: FAILURE_PRESERVED')
    print('TIMERS_AND_VIDEOS: UNCHANGED')
    print('ROLLBACK_WRAPPER:',backup)

if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,subprocess.SubprocessError) as exc:
        print('DURDU:',exc,file=sys.stderr)
        sys.exit(2)