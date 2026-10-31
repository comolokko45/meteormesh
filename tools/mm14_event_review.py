#!/usr/bin/env python3
"""MeteorMesh v0.1 append-only, per-camera candidate review indexes.

Runs after RC1.4 analysis. No clips, original copies, deletion, external upload or
scientific confirmation. A bad/offline camera must not suppress another camera.
Only verified HDD mount can receive reports; failure never authorizes fallback.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from zoneinfo import ZoneInfo

HOME=Path.home()
DATA=Path('/srv/meteor-data')
HUB=HOME/'.local/share/meteormesh/releases/rc14-20261031-split/MeteorHub'
SCOPE=HOME/'.local/share/meteormesh/staging/fullnight/night-scope-current.json'
CAMERAS={'cam-viva','cam-ruby','cam-surya'}
HEX=re.compile(r'[a-f0-9]{64}\Z')
SEG=re.compile(r'(?:viva|ruby|surya)_(\d{8}T\d{6}Z)_[a-f0-9]{12}\Z')
JOURNAL_PREFIX='EVENT_REVIEW_INDEX:'


def digest(b: bytes)->str: return hashlib.sha256(b).hexdigest()


def read_scope(path: Path)->dict:
    if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
        raise RuntimeError('INSECURE_OR_MISSING_NIGHT_SCOPE')
    scope=json.loads(path.read_text())
    if (scope.get('schema')!='meteormesh.night_scope.v1' or scope.get('status')!='ARMED' or
            scope.get('automatic_run_allowed') is not True or scope.get('ingest_scope_enforced') is not True):
        raise RuntimeError('UNAPPROVED_SCOPE')
    if set(scope.get('excluded_segments') or {})!=CAMERAS:
        raise RuntimeError('INVALID_EXCLUSIONS')
    for camera,items in scope['excluded_segments'].items():
        if not isinstance(items,dict) or any(not isinstance(k,str) or not isinstance(v,str) or not HEX.fullmatch(v)
                                                 for k,v in items.items()):
            raise RuntimeError('INVALID_EXCLUSION_DIGEST')
    if any(type(scope.get(k)) is not int for k in ('start_utc_ms','end_utc_ms')):
        raise RuntimeError('BAD_SCOPE_TIMES')
    start,end=scope['start_utc_ms'],scope['end_utc_ms']
    if not 1700000000000<start<end<=start+16*3600*1000:
        raise RuntimeError('BAD_SCOPE_WINDOW')
    baseline=scope.get('baseline_created_utc')
    if not isinstance(baseline,str): raise RuntimeError('BAD_BASELINE')
    start_base=dt.datetime.fromisoformat(baseline.replace('Z','+00:00'))
    if start_base.tzinfo is None: raise RuntimeError('NAIVE_BASELINE')
    scope['_baseline_ms']=int(start_base.timestamp()*1000)
    if scope['_baseline_ms']>=start: raise RuntimeError('LATE_BASELINE')
    return scope


def night_key(scope:dict,camera:str,seg:str,sha:str)->str|None:
    if camera not in CAMERAS or not HEX.fullmatch(sha or '') or not SEG.fullmatch(seg or ''):
        return None
    excluded=scope['excluded_segments'][camera].get(seg)
    if excluded is not None:
        if excluded!=sha: raise RuntimeError('BASELINE_SOURCE_CHANGED')
        return None
    stamp=dt.datetime.strptime(SEG.fullmatch(seg).group(1),'%Y%m%dT%H%M%SZ').replace(tzinfo=dt.timezone.utc)
    millis=int(stamp.timestamp()*1000)
    if not scope['_baseline_ms']<millis<scope['end_utc_ms']:
        return None
    local=stamp.astimezone(ZoneInfo('Europe/Istanbul'))
    if not (local.hour>=19 or local.hour<5):
        return None
    day=local.date() if local.hour>=19 else local.date()-dt.timedelta(days=1)
    return day.strftime('%Y%m%d')


def read_regular(guard,relative:str,max_bytes:int)->bytes:
    """Read through a pinned dirfd, no symlinks or persistent FD accumulation."""
    parts=relative.split('/')
    if not parts or any(x in ('','.','..') for x in parts):
        raise RuntimeError('INVALID_RELATIVE_READ_PATH')
    guard.verify()
    parent=guard._open_parent(parts[:-1],create=False)
    try:
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_dev!=guard._initial_dev or st.st_size>max_bytes:
                raise RuntimeError('UNSAFE_OR_OVERSIZED_SOURCE')
            guard.verify()
            data=os.read(fd,max_bytes+1)
            if len(data)>max_bytes or len(data)!=st.st_size:
                raise RuntimeError('SOURCE_CHANGED_OR_TOO_LARGE')
            return data
        finally:os.close(fd)
    finally:os.close(parent)


def check_source_size(guard,relative:str,expected:int):
    parts=relative.split('/')
    if not parts or any(x in ('','.','..') for x in parts):
        raise RuntimeError('INVALID_SOURCE_PATH')
    guard.verify()
    parent=guard._open_parent(parts[:-1],create=False)
    try:
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_dev!=guard._initial_dev or st.st_size!=expected or expected<=0:
                raise RuntimeError('SOURCE_MISSING_OR_WRONG_SIZE')
            guard.verify()
        finally:os.close(fd)
    finally:os.close(parent)


def parse_candidates(raw:bytes)->list[dict]:
    obj=json.loads(raw)
    if not isinstance(obj,dict) or not isinstance(obj.get('results'),list):
        raise RuntimeError('BAD_MDRF_SCHEMA')
    fps_raw=(obj.get('basic_info') or {}).get('fps') if isinstance(obj.get('basic_info'),dict) else None
    try: fps=float(fps_raw)
    except (TypeError,ValueError):fps=0.0
    if not (math.isfinite(fps) and fps>0): fps=0.0
    out=[]
    for rec in obj['results']:
        if not isinstance(rec,dict) or not isinstance(rec.get('target'),list):continue
        for target in rec['target']:
            if not isinstance(target,dict):continue
            frame=target.get('start_frame')
            if not isinstance(frame,int) or isinstance(frame,bool) or frame<0: frame=None
            seconds=round(frame/fps,3) if frame is not None and fps else None
            score=target.get('score')
            if not isinstance(score,(float,int)) or isinstance(score,bool) or not math.isfinite(score):score=None
            out.append({'frame':frame,'relative_seconds_estimate':seconds,
                        'score':score,'category':str(target.get('category','unknown'))[:80]})
    return out


def make_manifest(camera:str,seg:str,video_sha:str,result_sha:str,job_id:int,
                  target_index:int,target:dict,night:str)->tuple[str,bytes]:
    key=f'{camera}:{seg}:{video_sha}:{result_sha}:{target_index}'
    packet_id='e_'+digest(key.encode())[:24]
    t=target['relative_seconds_estimate']
    review_window={'start_seconds':max(0,round(t-30,3)),'duration_seconds':60,
                   'exact_pts_calibrated':False} if t is not None else None
    payload={'schema':'meteormesh.event.review-index.v1','package_id':packet_id,
             'night_date_local':night,'camera_id':camera,'segment_id':seg,
             'source_video_relative_path':f'{camera}/{seg}.mp4',
             'source_video_sha256_at_ingest':video_sha,
             'detector_result_sha256_verified':result_sha,
             'detector_job_id':job_id,'candidate_index':target_index,
             'candidate':target,'suggested_60s_review_window':review_window,
             'source_video_rehashed_now':False,'clip_generated':False,
             'scientific_meteor_confirmed':False,'multi_camera_match_confirmed':False,
             'drive_archived':False,'retention_permission':False,'delete_allowed':False}
    raw=(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':'))+'\n').encode()
    return f'event-review/{night}/{packet_id}.json',raw


def export(guard,db:sqlite3.Connection,scope:dict)->dict:
    allrows=db.execute('''SELECT j.id,j.device_id,j.segment_id,j.video_sha256,j.state,
                         r.result_path,r.result_sha256,s.size_bytes
                         FROM jobs j JOIN detector_runs r ON r.job_id=j.id
                         JOIN segments s ON s.device_id=j.device_id AND s.segment_id=j.segment_id
                              AND s.sha256=j.video_sha256 AND s.state='INGEST_VERIFIED'
                         WHERE j.state='SUCCEEDED' ORDER BY j.id DESC''').fetchall()
    seen=set(); made_dirs=set(); total=0; duplicates=0; errors=[]; per={c:0 for c in sorted(CAMERAS)}
    # Guard entire HDD export; never fall back to /home or SSD.
    for jid,camera,segment,sha,state,result_path,result_sha,video_size in allrows:
        try:
            night=night_key(scope,camera,segment,sha)
            if night is None: continue
            key=(camera,segment,sha)
            if key in seen:continue
            seen.add(key)
            if not isinstance(result_sha,str) or not HEX.fullmatch(result_sha):
                raise RuntimeError('BAD_RESULT_SHA')
            canonical=Path(guard.policy.root)/'detector-results'/camera
            p=Path(result_path)
            if (not p.is_absolute() or p.parent!=canonical or
                    not p.name.startswith(segment+'_') or not p.name.endswith('.json')):
                raise RuntimeError('BAD_RESULT_LOCATION')
            raw=read_regular(guard,f'detector-results/{camera}/{p.name}',20*1024*1024)
            if digest(raw)!=result_sha:raise RuntimeError('RESULT_SHA_MISMATCH')
            # Verify source existence/size without expensive MP4 re-hash; no false verification claims.
            check_source_size(guard,f'{camera}/{segment}.mp4',video_size)
            side=json.loads(read_regular(guard,f'{camera}/{segment}.json',1024*1024))
            if side.get('segment_id')!=segment or side.get('sha256')!=sha:
                raise RuntimeError('SIDECAR_IDENTITY_MISMATCH')
            candidates=parse_candidates(raw)
            if not candidates:continue
            if night not in made_dirs:
                guard.create_dir('event-review')
                guard.create_dir('event-review/'+night)
                made_dirs.add(night)
            for index,target in enumerate(candidates):
                relative,packet=make_manifest(camera,segment,sha,result_sha,jid,index,target,night)
                try:
                    guard.write_new(relative,[packet],expected_bytes=len(packet))
                    total+=1;per[camera]+=1
                except Exception as exc:
                    # Only an already-existing file is idempotent; all other guard denials are fatal.
                    if type(exc).__name__!='StorageDenied' or str(exc)!='EXCLUSIVE_CREATE_FAILED':
                        raise
                    prior=read_regular(guard,relative,16*1024)
                    if prior!=packet:raise RuntimeError('INDEX_CONFLICT') from exc
                    duplicates+=1
        except (RuntimeError,ValueError,OSError,sqlite3.Error,TypeError,KeyError) as exc:
            errors.append({'device':camera,'job_id':jid,'reason':type(exc).__name__+': '+str(exc)[:80]})
            # Failure of one camera does not suppress others; HDD mount failure still prevents further writes.
            if type(exc).__name__=='StorageDenied': raise
            continue
    return {'created':total,'existing':duplicates,'per_camera':per,
            'failed_sources':len(errors),'errors':errors[:8],
            'clip_generated':False,'originals_deleted':False,'archives_uploaded':False}


def main()->int:
    if HUB.is_symlink() or not HUB.is_dir() or not (HUB/'storage_guard.py').is_file():
        print('EVENT_REVIEW_UNAVAILABLE: missing verified RC14 release')
        return 0
    sys.path.insert(0,str(HUB))
    from storage_guard import GuardedStorage, StoragePolicy, StorageDenied
    try:
        scope=read_scope(SCOPE)
        with GuardedStorage(StoragePolicy()) as guard:
            db=guard.connect_sqlite('meteorhub.sqlite3',read_only=True)
            db.execute('PRAGMA query_only=ON')
            report=export(guard,db,scope)
        print(JOURNAL_PREFIX+' '+json.dumps(report,sort_keys=True,ensure_ascii=False))
    except (RuntimeError,OSError,ValueError,sqlite3.Error,StorageDenied) as exc:
        print('EVENT_REVIEW_UNAVAILABLE: '+type(exc).__name__+': '+str(exc)[:150])
    return 0  # observer never overrides analysis exit code

if __name__=='__main__':raise SystemExit(main())