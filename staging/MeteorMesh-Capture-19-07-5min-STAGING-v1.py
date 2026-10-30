#!/usr/bin/env python3
"""MeteorMesh: one 5-minute user-systemd capture heartbeat (19:00--07:00 Istanbul).

Installer NEVER enables timers or starts recording. Worker (--tick) manages only
recordings it initiated, never deletes phone media, and never accesses the HDD.
LAN HTTP and the installed fixed lab token remain unchanged by user choice.
"""
from __future__ import annotations
import argparse
import datetime as dt
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

HOME = Path.home()
CONFIG = HOME / '.config/meteormesh/devices-three-lab-rc12.json'
TOKENS = HOME / '.config/meteormesh/tokens.env'
TOOL = HOME / '.local/lib/meteormesh/meteor-capture-loop.py'
UNIT_DIR = HOME / '.config/systemd/user'
SERVICE = UNIT_DIR / 'meteormesh-capture.service'
TIMER = UNIT_DIR / 'meteormesh-capture.timer'
STATE = HOME / '.local/state/meteormesh/capture-owned.json'
TZ = ZoneInfo('Europe/Istanbul')
EXPECTED = {'cam-viva':'viva', 'cam-ruby':'ruby', 'cam-surya':'surya'}


def inside_window(at=None):
    at = (at or dt.datetime.now(dt.timezone.utc)).astimezone(TZ)
    return at.hour >= 19 or at.hour < 7


def read_devices():
    if not CONFIG.is_file() or CONFIG.is_symlink() or not TOKENS.is_file() or TOKENS.is_symlink():
        raise RuntimeError('Device or token configuration missing')
    rows = json.loads(CONFIG.read_text()).get('devices', [])
    if len(rows) != 3 or {r.get('id') for r in rows} != set(EXPECTED):
        raise RuntimeError('Expected exactly three configured phones')
    secrets = dict(x.split('=',1) for x in TOKENS.read_text().splitlines()
                   if '=' in x and not x.lstrip().startswith('#'))
    result=[]
    for r in rows:
        ident=r['id']; host=r.get('host'); port=r.get('port',8080)
        token=secrets.get(r.get('token_env'), '')
        if (r.get('enabled') is not True or r.get('transport') != 'http_lab'
            or not re.fullmatch(r'192\.168\.0\.\d{1,3}',str(host)) or port!=8080
            or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}',token)):
            raise RuntimeError('Unexpected configuration for '+ident)
        result.append({'id':ident,'device_id':EXPECTED[ident], 'host':host,'port':port,'token':token})
    return result


def api(device, path, method='GET', timeout=6):
    conn = http.client.HTTPConnection(device['host'],device['port'],timeout=timeout)
    try:
        headers={'Authorization':'Bearer '+device['token']}
        if method == 'POST': headers['Content-Length']='0'
        conn.request(method,path,body=b'' if method=='POST' else None,headers=headers)
        res=conn.getresponse()
        data=res.read(1024*1024)
        if res.status!=(202 if method=='POST' else 200):
            raise RuntimeError(f'{device["id"]}: {path} HTTP {res.status}')
        obj=json.loads(data)
        if not isinstance(obj,dict): raise ValueError('Unexpected JSON')
        return obj
    finally:
        conn.close()


def health(d):
    h=api(d,'/api/v1/health')
    if (h.get('device_id')!=d['device_id'] or
        not str(h.get('node_version','')).startswith('0.10.')):
        raise RuntimeError(f'{d["id"]}: wrong device identity/version')
    return h


def wait_for(d, want_recording, timeout=20):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            h=health(d)
            recorded=str(h.get('state','')).startswith('recording')
            if recorded==want_recording:
                return True
        except (OSError,TimeoutError):
            pass
        time.sleep(1)
    return False


def read_state():
    if not STATE.exists(): return {'schema':'meteormesh.capture-owned.v1','owned':{}}
    if STATE.is_symlink() or STATE.stat().st_mode & 0o077:
        raise RuntimeError('Unsafe owned-state file')
    doc=json.loads(STATE.read_text())
    if doc.get('schema')!='meteormesh.capture-owned.v1' or not isinstance(doc.get('owned'),dict):
        raise RuntimeError('Unknown owned-state schema')
    if set(doc['owned'])-set(EXPECTED): raise RuntimeError('Unknown owned device')
    return doc


def save_state(doc):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    if STATE.is_symlink(): raise RuntimeError('Refusing symlink in owned-state path')
    fd,tmp=tempfile.mkstemp(prefix='.capture-owned-',dir=STATE.parent)
    try:
        with os.fdopen(fd,'w') as f:
            os.fchmod(f.fileno(),0o600)
            json.dump(doc,f,ensure_ascii=False,indent=2)
            f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,STATE)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def process_one(d, doc, night, fetch=health, send=api, await_state=wait_for, persist=save_state):
    ident=d['id']; h=fetch(d)
    state=str(h.get('state',''))
    owned=ident in doc['owned']
    if night:
        if state.startswith('recording'):
            return 'RECORDING_OWNED' if owned else 'RECORDING_MANUAL_UNTOUCHED'
        if state not in ('idle','armed_idle'):
            raise RuntimeError(f'{ident}: unexpected state {state}')
        if h.get('remote_camera_ready') is not True or h.get('remote_control_enabled') is not True:
            raise RuntimeError(f'{ident}: remote camera/control unavailable')
        if int(h.get('free_bytes') or 0)<2*1024**3:
            raise RuntimeError(f'{ident}: <2GiB free storage')
        # Persist ownership intent BEFORE sending HTTP start, to survive an HTTP timeout.
        if not owned:
            doc['owned'][ident]={'since_utc':dt.datetime.now(dt.timezone.utc).isoformat()}
            persist(doc)
        send(d,'/api/v1/control/start','POST')
        if await_state(d,True):return 'RECORDING_STARTED'
        raise RuntimeError(f'{ident}: start accepted, recording not yet confirmed; retry next tick')
    # Daytime: stop ONLY sessions we started, preserving any user-initiated recording.
    if not owned:
        return 'DAY_MANUAL_UNTOUCHED' if state.startswith('recording') else 'DAY_IDLE'
    if state.startswith('recording'):
        send(d,'/api/v1/control/stop','POST')
        if not await_state(d,False):
            raise RuntimeError(f'{ident}: stop not yet confirmed; retry next tick')
    elif state not in ('idle','armed_idle'):
        raise RuntimeError(f'{ident}: cannot confirm stopped: {state}')
    del doc['owned'][ident]
    persist(doc)
    return 'STOPPED_AND_RELEASED'


def run_once(check_only):
    devices=read_devices()
    night=inside_window()
    print('CAPTURE_WINDOW:', 'NIGHT_19_07' if night else 'DAY_07_19',flush=True)
    doc=read_state()
    errors=0
    for d in devices:
        try:
            if check_only:
                h=health(d)
                print(f'{d["id"]}: {h.get("state")}, CAMERA={h.get("remote_camera_ready")}, FREE_GB={int(h.get("free_bytes") or 0)//(1024**3)}, OWNED={d["id"] in doc["owned"]}',flush=True)
            else:
                print(f'{d["id"]}: {process_one(d,doc,night)}',flush=True)
        except Exception as exc:
            errors+=1
            print(f'{d["id"]}: ERROR {type(exc).__name__}: {str(exc)[:120]}',flush=True)
    if errors:raise RuntimeError(f'{errors} node(s) failed; retry on next 5-minute tick')
    print('CAPTURE_CHECK: PASS' if check_only else 'CAPTURE_TICK: PASS',flush=True)


def unit_text():
    return ('[Unit]\nDescription=MeteorMesh three-phone night capture tick\nAfter=network-online.target\nWants=network-online.target\n\n'
        '[Service]\nType=oneshot\nExecStart=/usr/bin/python3 -B '+str(TOOL)+' --tick\n'
        'Environment=PYTHONDONTWRITEBYTECODE=1\nTimeoutStartSec=90\n')


def timer_text():
    return ('[Unit]\nDescription=MeteorMesh night capture every five minutes\n\n'
        '[Timer]\nOnCalendar=*-*-* *:0/5:00 Europe/Istanbul\nAccuracySec=30s\nPersistent=true\n'
        'Unit=meteormesh-capture.service\n\n[Install]\nWantedBy=timers.target\n')


def write_new_or_compare(path,content,mode):
    data=content.encode('utf-8')
    if path.is_symlink():raise RuntimeError('Unexpected symlink: '+str(path))
    if path.exists():
        if path.read_bytes()!=data:raise RuntimeError('Existing unit has different content: '+str(path))
        return 'ALREADY_IDENTICAL'
    path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    with os.fdopen(fd,'wb') as f:
        f.write(data);f.flush();os.fsync(f.fileno())
    return 'CREATED'


def systemctl(verb,unit):
    p=subprocess.run(['systemctl','--user',verb,unit],capture_output=True,text=True,timeout=12)
    return p.stdout.strip(),p.returncode


def install():
    if os.geteuid()==0:raise RuntimeError('Do not run as root')
    for unit in ('meteormesh-morning.timer','meteormesh-morning.service'):
        act,_=systemctl('is-active',unit)
        if act!='inactive':raise RuntimeError(unit+' must remain inactive')
    morning,_=systemctl('is-enabled','meteormesh-morning.timer')
    if morning!='disabled':raise RuntimeError('Morning timer must remain disabled')
    for unit in ('meteormesh-capture.timer','meteormesh-capture.service'):
        active,_=systemctl('is-active',unit)
        if active not in ('inactive','unknown'):raise RuntimeError('Capture unit unexpectedly active')
    # Check each node before adding units; no side effects.
    run_once(check_only=True)
    path=Path(__file__)
    source=path.read_text()
    files=((TOOL,source,0o600),(SERVICE,unit_text(),0o600),(TIMER,timer_text(),0o600))
    for target,body,mode in files:
        result=write_new_or_compare(target,body,mode)
        print('STAGED:',target.name,result)
    valid=subprocess.run(['systemd-analyze','verify',str(SERVICE),str(TIMER)],capture_output=True,text=True,timeout=20)
    if valid.returncode:raise RuntimeError('Unit validation failed: '+valid.stderr[:400])
    reload=subprocess.run(['systemctl','--user','daemon-reload'],capture_output=True,text=True,timeout=12)
    if reload.returncode:raise RuntimeError('daemon-reload failed: '+reload.stderr[:200])
    enabled,_=systemctl('is-enabled','meteormesh-capture.timer')
    active,_=systemctl('is-active','meteormesh-capture.timer')
    if enabled=='enabled' or active=='active':raise RuntimeError('Capture timer was unexpectedly enabled')
    print('CAPTURE_SCHEDULE: STAGED_DISABLED')
    print('MORNING_TIMER: DISABLED')
    print('CAPTURE_SCHEDULE: ONE_TIMER_EVERY_5_MIN; WINDOW_19_07')
    print('NO_PHONE_ACTIONS; NO_VIDEO_OR_DATABASE_WRITES')
    print('CAPTURE_STAGING: PASS')


def self_test():
    tz=TZ
    for hour,expected in [(0,True),(6,True),(7,False),(18,False),(19,True),(23,True)]:
        assert inside_window(dt.datetime(2026,10,31,hour,tzinfo=tz))==expected
    calls=[]
    class Fake:
        state='idle'
    fake=Fake()
    device={'id':'cam-viva'}
    def h(d):return {'state':fake.state,'remote_camera_ready':True,'remote_control_enabled':True,'free_bytes':50*1024**3}
    def s(d,path,method):calls.append((path,method))
    def wait(d,is_record):
        fake.state='recording:xyz' if is_record else 'idle'
        return True
    saved=[]
    def p(doc):saved.append(json.loads(json.dumps(doc)))
    doc={'schema':'meteormesh.capture-owned.v1','owned':{}}
    assert process_one(device,doc,True,h,s,wait,p)=='RECORDING_STARTED'
    assert 'cam-viva' in doc['owned']
    assert process_one(device,doc,True,h,s,wait,p)=='RECORDING_OWNED'
    assert process_one(device,doc,False,h,s,wait,p)=='STOPPED_AND_RELEASED'
    assert not doc['owned']
    fake.state='recording:manual'
    assert process_one(device,doc,False,h,s,wait,p)=='DAY_MANUAL_UNTOUCHED'
    assert len(calls)==2 and calls[0][0].endswith('/start') and calls[1][0].endswith('/stop')
    print('CAPTURE_SELFTEST: PASS (6 window cases, 4 transitions, start/stop ownership)')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--self-test',action='store_true')
    group.add_argument('--check',action='store_true')
    group.add_argument('--install',action='store_true')
    group.add_argument('--tick',action='store_true')
    args=parser.parse_args()
    try:
        if args.self_test:self_test()
        elif args.check:run_once(check_only=True)
        elif args.install:install()
        elif args.tick:run_once(check_only=False)
    except (RuntimeError,OSError,ValueError,KeyError,subprocess.TimeoutExpired) as exc:
        print('CAPTURE_STOP:',exc,file=sys.stderr)
        sys.exit(2)
