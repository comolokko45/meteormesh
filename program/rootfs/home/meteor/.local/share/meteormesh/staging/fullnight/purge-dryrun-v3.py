#!/usr/bin/python3
"""MeteorMesh: salt okunur guvenli silme denetimi."""
import hashlib, json, os, re, shutil, sqlite3
import stat, subprocess, sys
from pathlib import Path
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo

ROOT=Path('/srv/meteor-data')
SCOPE=Path.home()/'.local/share/meteormesh/staging/fullnight/night-scope-current.json'
HUB=Path.home()/'.local/share/meteormesh/releases/rc152-20261101-bounded-retry/MeteorHub'
UUID='444a8a6d-090a-456c-8a9d-d99665c32574'
CAM={'cam-surya':'surya','cam-ruby':'ruby'}
PAT=re.compile(r'^(surya|ruby)_(\d{8}T\d{6}Z)_[0-9a-f]{12}$')
HEX=re.compile(r'[0-9a-f]{64}\Z')
TZ=ZoneInfo('Europe/Istanbul')

def valid_id(dev,seg,sha,start,end):
    m=PAT.fullmatch(seg) if isinstance(seg,str) else None
    if dev not in CAM or not m or m[1]!=CAM[dev]:
        return False
    if not isinstance(sha,str) or not HEX.fullmatch(sha):
        return False
    t=datetime.strptime(
        m[2],'%Y%m%dT%H%M%SZ'
    ).replace(tzinfo=timezone.utc)
    return start<=t<end

def host_file(dev,seg,sha,size,metadata):
    m=PAT.fullmatch(seg) if isinstance(seg,str) else None
    if (dev not in CAM or m is None or
        m.group(1)!=CAM[dev] or
        not isinstance(sha,str) or HEX.fullmatch(sha) is None):
        return 'REJECT_IDENTITY'
    folder=ROOT/dev
    if folder.is_symlink() or not folder.is_dir():
        return 'REJECT_DIRECTORY'
    if type(size) is not int or size<=0:
        return 'REJECT_SIZE'

    path=folder/(seg+'.mp4')
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                return 'REJECT_FILE_TYPE'
            if st.st_size!=size or st.st_nlink!=1:
                return 'REJECT_FILE_SIZE'

            digest=hashlib.sha256()
            while block:=os.read(fd,1024*1024):
                digest.update(block)

            st2=os.fstat(fd)
            if (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)!=(
                st2.st_dev,st2.st_ino,st2.st_size,st2.st_mtime_ns
            ):
                return 'REJECT_CHANGED'

            check=os.stat(path,follow_symlinks=False)
            if (check.st_dev,check.st_ino,check.st_mtime_ns)!=(
                st2.st_dev,st2.st_ino,st2.st_mtime_ns
            ):
                return 'REJECT_REPLACED'
        finally:
            os.close(fd)

        if digest.hexdigest()!=sha:
            return 'REJECT_SHA256'

        stored=json.loads(metadata)
        if int(stored['format']['size'])!=size:
            return 'REJECT_DB_PROBE'

        if not any(
            x.get('codec_type')=='video'
            and x.get('codec_name')=='h264'
            for x in stored['streams']
        ):
            return 'REJECT_DB_VIDEO'

        probe=subprocess.run(
            ['ffprobe','-v','error',
             '-show_entries',
             'stream=codec_type,codec_name,width,height:format=size,duration',
             '-of','json',str(path)],
            capture_output=True,text=True,timeout=30
        )
        if probe.returncode:
            return 'REJECT_FFPROBE'

        video=json.loads(probe.stdout)
        if int(video['format']['size'])!=size:
            return 'REJECT_VIDEO_SIZE'
        if float(video['format']['duration'])<=1:
            return 'REJECT_VIDEO_DURATION'
        if not any(
            x.get('codec_type')=='video'
            and x.get('codec_name')=='h264'
            for x in video['streams']
        ):
            return 'REJECT_VIDEO_CODEC'

        return 'HOST_VERIFIED_NOT_DELETE_AUTHORIZED'

    except (OSError,ValueError,KeyError,TypeError,
            subprocess.TimeoutExpired):
        return 'REJECT_FILE_OR_METADATA'

def main():
    now=datetime.now(TZ)
    day=now.date() if now.hour>=19 else (
        now.date()-timedelta(days=1)
    )
    night=f'{day:%Y%m%d}-night'
    start=datetime.combine(
        day,time(19),TZ
    ).astimezone(timezone.utc)
    end=datetime.combine(
        day+timedelta(days=1),time(5),TZ
    ).astimezone(timezone.utc)

    print('MODE: DRY_RUN_ONLY')
    print('PHONE_DELETE: OFF')
    print('HDD_DELETE: OFF')
    print('EXPECTED_NIGHT:',night)

    if ROOT.is_symlink() or not ROOT.is_dir():
        raise RuntimeError('HDD_MISSING')

    mount=subprocess.run(
        ['findmnt','-n','-o','TARGET,FSTYPE,UUID',
         '--mountpoint',str(ROOT)],
        capture_output=True,text=True,check=True,timeout=10
    )
    if mount.stdout.strip().split()!=[
        str(ROOT),'ext4',UUID
    ]:
        raise RuntimeError('HDD_UUID_MISMATCH')

    if shutil.disk_usage(ROOT).free<100*1024**3:
        raise RuntimeError('HDD_FREE_BELOW_100_GIB')

    if SCOPE.is_symlink() or not SCOPE.is_file():
        raise RuntimeError('UNSAFE_SCOPE')
    if SCOPE.stat().st_mode&0o077:
        raise RuntimeError('SCOPE_PERMISSIONS')

    s=json.loads(SCOPE.read_text())

    if (
        s.get('night_id')!=night
        or s.get('status')!='ARMED'
        or s.get('automatic_run_allowed') is not True
        or s.get('ingest_scope_enforced') is not True
        or s.get('queue_scope_enforced') is not True
        or s.get('retention_auto_delete') is not False
        or (s.get('start_utc_ms'),s.get('end_utc_ms'))!=(
            int(start.timestamp()*1000),
            int(end.timestamp()*1000)
        )
    ):
        raise RuntimeError('WAIT_FOR_CORRECT_NIGHT_SCOPE')

    if not (HUB/'night_scope.py').is_file():
        raise RuntimeError('SCOPE_CODE_MISSING')

    sys.path.insert(0,str(HUB))
    from night_scope import load_night_scope
    scope=load_night_scope(SCOPE)

    db=sqlite3.connect(
        f'file:{ROOT}/meteorhub.sqlite3?mode=ro',
        uri=True
    )
    try:
        db.execute('PRAGMA query_only=ON')
        rows=db.execute("""
            SELECT device_id,segment_id,sha256,
                   size_bytes,ffprobe_json
            FROM segments
            WHERE state='INGEST_VERIFIED'
        """).fetchall()
    finally:
        db.close()

    matches=[
        r for r in rows
        if valid_id(r[0],r[1],r[2],start,end)
        and scope.allows(r[0],r[1],r[2])
    ]

    print('CURRENT_NIGHT_DB_ROWS:',len(matches))

    if not matches:
        print('RESULT: NO_CANDIDATE')
        return

    r=sorted(matches)[0]
    print('SAMPLE:',r[0],r[1])
    print('CHECK:',host_file(*r))
    print('OTHERS_NOT_CHECKED:',len(matches)-1)
    print('REMOTE_READY_RECEIPT: NOT_CHECKED')
    print('RESULT: DRY_RUN_ONLY; DELETE OFF')

if __name__=='__main__':
    if '--self-test' in sys.argv:
        a=datetime(2026,11,1,16,tzinfo=timezone.utc)
        b=datetime(2026,11,2,2,tzinfo=timezone.utc)
        assert valid_id(
            'cam-ruby',
            'ruby_20261101T163000Z_012345abcdef',
            'a'*64,a,b
        )
        assert not valid_id(
            'cam-ruby',
            'ruby_20261101T015149Z_012345abcdef',
            'a'*64,a,b
        )
        assert not valid_id(
            'cam-ruby',
            'surya_20261101T163000Z_012345abcdef',
            'a'*64,a,b
        )
        assert not valid_id(
            'cam-viva',
            'viva_20261101T163000Z_012345abcdef',
            'a'*64,a,b
        )
        print('4/4 KIMLIK_VE_GECE_TESTI: PASS')
        print('DELETE: OFF')
    else:
        try:
            main()
        except Exception as e:
            print('FAIL_CLOSED:',type(e).__name__,str(e))
            print('DELETE: OFF')
            sys.exit(2)
