#!/usr/bin/env python3
"""Prepare one recurring nightly RC1.3 policy and INACTIVE service candidate.

No Android settings, HTTPS/TLS work, production release changes, timer activation,
video access or SQLite writes. Only ~/.local/share/meteormesh/staging/fullnight.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Europe/Istanbul')
UTC = dt.timezone.utc
HOME = Path.home()
STAGE = HOME / '.local/share/meteormesh/staging/fullnight'
BASELINE = STAGE / 'night-scope-v1.json'
CURRENT = STAGE / 'night-scope-current.json'
CODE = STAGE / 'meteor-auto-night-policy.py'
CANDIDATE = STAGE / 'meteormesh-morning-rc13.service'
DAILY = STAGE / 'meteormesh-morning-rc13-daily.service'
EXPECTED = {'cam-viva': 10, 'cam-ruby': 2, 'cam-surya': 2}
GENERATOR = 'meteormesh.daily-night.v1'


def parse_instant(text):
    val = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
    if val.tzinfo is None:
        raise ValueError('Timezone required')
    return val.astimezone(UTC)


def read_baseline(path=BASELINE):
    if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
        raise RuntimeError('Baseline policy missing, symlinked, or not private')
    raw = path.read_bytes()
    p = json.loads(raw)
    if (p.get('schema') != 'meteormesh.night_scope.v1' or p.get('status') != 'DISARMED'
        or p.get('automatic_run_allowed') is not False
        or p.get('ingest_scope_enforced') is not False
        or p.get('queue_scope_enforced') is not False):
        raise RuntimeError('Original DISARMED baseline changed; stop')
    entries = p.get('excluded_segments', {})
    if set(entries) != set(EXPECTED) or any(len(entries[k]) != v for k,v in EXPECTED.items()):
        raise RuntimeError('Original 14-item exclusion list changed; stop')
    for v in entries.values():
        if not isinstance(v, dict) or not all(isinstance(x, str) and re.fullmatch('[0-9a-f]{64}', x) for x in v.values()):
            raise RuntimeError('Invalid exclusion digests')
    when = parse_instant(p['baseline_created_utc'])
    return p, hashlib.sha256(raw).hexdigest(), when


def night_for(local_now):
    here = local_now.astimezone(TZ)
    # At 09:00, select previous 19:00--07:00 session.
    evening = here.date() if here.hour >= 19 else here.date() - dt.timedelta(days=1)
    start = dt.datetime.combine(evening, dt.time(19,0), TZ).astimezone(UTC)
    end = dt.datetime.combine(evening+dt.timedelta(days=1), dt.time(7,0), TZ).astimezone(UTC)
    return evening, start, end


def build(local_now, original, fingerprint, baseline):
    evening, start, end = night_for(local_now)
    # First partial night cannot claim footage taken before the known 14-file baseline.
    start = max(start, baseline + dt.timedelta(seconds=1))
    if start >= end or (end-start) > dt.timedelta(hours=16):
        raise RuntimeError('Invalid or already-finished night window; stop')
    return {
        'schema':'meteormesh.night_scope.v1',
        'generator': GENERATOR,
        'status':'ARMED',
        'night_id':evening.strftime('%Y%m%d')+'-night',
        'start_utc_ms':int(start.timestamp()*1000),
        'end_utc_ms':int(end.timestamp()*1000),
        'baseline_created_utc':original['baseline_created_utc'],
        'baseline_sha256':original['baseline_sha256'],
        'baseline_policy_sha256':fingerprint,
        'excluded_segments':original['excluded_segments'],
        'ingest_scope_enforced':True,
        'queue_scope_enforced':True,
        'automatic_run_allowed':True,
        'retention_auto_delete':False,
        'local_night_window':'19:00-07:00 Europe/Istanbul',
    }


def check_current(existing, new):
    if existing.get('generator') != GENERATOR or existing.get('baseline_policy_sha256') != new['baseline_policy_sha256']:
        raise RuntimeError('Existing policy is not ours: no overwrite')
    if existing.get('night_id') > new['night_id']:
        raise RuntimeError('Clock moved backwards: no overwrite')
    if existing.get('night_id') == new['night_id'] and existing != new:
        raise RuntimeError('Same night policy has changed: no overwrite')


def atomic_write(dst, doc):
    if dst.is_symlink():
        raise RuntimeError('Unsafe current policy symlink')
    if dst.exists():
        if not dst.is_file() or dst.stat().st_mode & 0o077:
            raise RuntimeError('Unsafe existing night policy')
        existing = json.loads(dst.read_text())
        check_current(existing, doc)
        if existing == doc:
            return 'UNCHANGED'
    data=(json.dumps(doc, ensure_ascii=False, indent=2)+'\n').encode()
    fd, temp = tempfile.mkstemp(prefix='.night-scope-', suffix='.tmp', dir=dst.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(),0o600)
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,dst)
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return 'WRITTEN'


def assert_scope_valid(src_dir, doc):
    sys.path.insert(0,str(src_dir))
    from night_scope import load_night_scope
    with tempfile.TemporaryDirectory(prefix='mm-night-validate-') as d:
        p=Path(d)/'scope.json';p.write_text(json.dumps(doc));p.chmod(0o600)
        s=load_night_scope(p)
        if s.night_id != doc['night_id']:
            raise RuntimeError('RC1.3 validation failed')
        for dev,ids in doc['excluded_segments'].items():
            for key,sha in ids.items():
                if s.allows(dev,key,sha):
                    raise RuntimeError('Baseline item unexpectedly allowed')
    sys.path.remove(str(src_dir))


def ensure_paths():
    dirs=list(STAGE.glob('night-rc13-*/MeteorHub'))
    if len(dirs)!=1 or not (dirs[0]/'night_scope.py').is_file():
        raise RuntimeError('Need exactly one RC1.3 staging source')
    return dirs[0]


def run_cmd(*args,timeout=12):
    return subprocess.run(args,capture_output=True,text=True,timeout=timeout)


def tests():
    original={'baseline_created_utc':'2026-10-30T17:18:57Z',
              'baseline_sha256':'a'*64,
              'excluded_segments':{
                  d:{f'{d[4:]}_20261029T120000Z_123456789abc':'a'*64}
                  for d in EXPECTED}}
    baseline=parse_instant(original['baseline_created_utc'])
    # First night: after baseline; next regular night; last known date; repeated call.
    a=build(dt.datetime(2026,10,30,21,27,tzinfo=TZ),original,'b'*64,baseline)
    assert a['night_id']=='20261030-night'
    assert a['start_utc_ms']==int((baseline+dt.timedelta(seconds=1)).timestamp()*1000)
    assert dt.datetime.fromtimestamp(a['end_utc_ms']/1000,UTC).astimezone(TZ).hour==7
    b=build(dt.datetime(2026,10,31,9,0,tzinfo=TZ),original,'b'*64,baseline)
    assert a==b
    c=build(dt.datetime(2026,11,1,9,0,tzinfo=TZ),original,'b'*64,baseline)
    assert c['night_id']=='20261031-night'
    assert c['end_utc_ms']-c['start_utc_ms']==12*3600*1000
    with tempfile.TemporaryDirectory() as temp:
        p=Path(temp)/'current.json'
        assert atomic_write(p,a)=='WRITTEN'
        assert atomic_write(p,a)=='UNCHANGED'
        assert atomic_write(p,c)=='WRITTEN'
        try:atomic_write(p,a)
        except RuntimeError:pass
        else:raise AssertionError('Clock rollback wrongly accepted')
    print('AUTO_NIGHT_TESTS: 7/7 PASS')


def verify_inactive():
    en=run_cmd('systemctl','--user','is-enabled','meteormesh-morning.timer').stdout.strip()
    ac=run_cmd('systemctl','--user','is-active','meteormesh-morning.timer').stdout.strip()
    svc=run_cmd('systemctl','--user','is-active','meteormesh-morning.service').stdout.strip()
    if (en,ac,svc)!=('disabled','inactive','inactive'):
        raise RuntimeError(f'Morning scheduling not in inactive preparation state: {en},{ac},{svc}')


def prepare():
    verify_inactive()
    src_dir=ensure_paths()
    if not CANDIDATE.is_file() or CANDIDATE.is_symlink():
        raise RuntimeError('RC1.3 service candidate missing')
    original,fingerprint,baseline=read_baseline()
    # Only a preview is generated during installation; daily policy is created by ExecStartPre.
    doc=build(dt.datetime.now(UTC),original,fingerprint,baseline)
    assert_scope_valid(src_dir,doc)
    text=CANDIDATE.read_text()
    if '--night-scope '+str(BASELINE) not in text:
        raise RuntimeError('Unexpected existing RC1.3 candidate')
    nextcmd='ExecStartPre=/usr/bin/python3 -B '+str(CODE)+' --write\n'
    text=text.replace('ExecStart=/usr/bin/python3',nextcmd+'ExecStart=/usr/bin/python3',1)
    text=text.replace('--night-scope '+str(BASELINE),'--night-scope '+str(CURRENT),1)
    if (text.count('ExecStartPre=')!=2 or text.count('ExecStart=')!=1 or
        text.count('--night-scope ')!=1 or 'METEORMESH_ISOLATED_TEST_ROOT' in text):
        raise RuntimeError('Candidate unit composition invalid')
    p=run_cmd('systemd-analyze','verify',str(CANDIDATE),timeout=15)
    if p.returncode: raise RuntimeError('Previous candidate verification failed: '+p.stderr[:150])
    with tempfile.TemporaryDirectory(prefix='mm-night-unit-') as d:
        trial=Path(d)/'meteormesh-morning-rc13-daily.service'
        trial.write_text(text)
        ck=run_cmd('systemd-analyze','verify',str(trial),timeout=15)
        if ck.returncode:raise RuntimeError('New unit syntax invalid: '+ck.stderr[:150])
    # install immutable staging helper; it is called by existing unit's pre-start later.
    if CODE.exists():
        if CODE.is_symlink() or CODE.read_bytes()!=Path(__file__).read_bytes():
            raise RuntimeError('Different recurring scope helper already present; no overwrite')
    else:
        fd=os.open(CODE,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as out:out.write(Path(__file__).read_bytes())
    if DAILY.exists():
        if DAILY.is_symlink() or DAILY.read_text()!=text:
            raise RuntimeError('Different daily unit exists; no overwrite')
    else:
        fd=os.open(DAILY,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as out:out.write(text)
    print('RC13_DAILY_CANDIDATE: READY')
    print('NEXT_NIGHT:', doc['night_id'])
    print('PREVIEW_START_UTC:',dt.datetime.fromtimestamp(doc['start_utc_ms']/1000,UTC).isoformat())
    print('PREVIEW_END_UTC:',dt.datetime.fromtimestamp(doc['end_utc_ms']/1000,UTC).isoformat())
    print('EXCLUDED_OLD_SEGMENTS: 14')
    print('DAILY_UNIT:',DAILY)
    print('POLICY_WRITTEN: NO (next service ExecStartPre creates it)')
    print('PRODUCTION_CHANGED: NO; MORNING_TIMER: disabled')
    print('ANDROID_HTTP_AND_TEST_KEY: UNCHANGED')


def write():
    src_dir=ensure_paths()
    original,fingerprint,baseline=read_baseline()
    doc=build(dt.datetime.now(UTC),original,fingerprint,baseline)
    assert_scope_valid(src_dir,doc)
    state=atomic_write(CURRENT,doc)
    print('NIGHT_SCOPE:',state,doc['night_id'])
    print('UTC_WINDOW:',doc['start_utc_ms'],doc['end_utc_ms'])
    print('EXCLUDED_OLD_SEGMENTS: 14')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare',action='store_true')
    group.add_argument('--write',action='store_true')
    group.add_argument('--test',action='store_true')
    args=parser.parse_args()
    try:
        if args.test:tests()
        if args.prepare:tests();prepare()
        if args.write:write()
    except (OSError,RuntimeError,ValueError,KeyError,subprocess.TimeoutExpired,AssertionError) as exc:
        print('DENIED:',exc,file=sys.stderr)
        sys.exit(2)