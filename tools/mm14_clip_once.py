#!/usr/bin/env python3
"""One manual, 60s TEST preview from one verified MeteorMesh review index.

Only the chosen camera's existing verified original and estimate are used.
Creates an isolated derived clip + receipt on UUID-verified HDD. No
recorder/service/agent/timer changes, no upload, no source video deletion.
"""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

DATA=Path('/srv/meteor-data')
HUB=Path.home()/'.local/share/meteormesh/releases/rc14-20261031-split/MeteorHub'
CAMERAS={'cam-ruby','cam-surya','cam-viva'}
ID=re.compile(r'e_[0-9a-f]{24}\Z')
DAY=re.compile(r'20[0-9]{6}\Z')
SEG=re.compile(r'(viva|ruby|surya)_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{12}\Z')
HEX=re.compile(r'[a-f0-9]{64}\Z')


def checked(cmd, *, pass_fds=()):
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=20,pass_fds=pass_fds)
    if r.returncode:
        raise RuntimeError('PROCESS_FAILED '+str(r.returncode)+' '+r.stderr.strip()[:160])
    return r.stdout


def duration_of(fd:int)->float:
    text=checked(['ffprobe','-v','error','-show_entries','format=duration',
                  '-of','default=nokey=1:noprint_wrappers=1',f'/proc/self/fd/{fd}'],pass_fds=(fd,))
    value=float(text.strip())
    if not 0.0<value<3600.0:raise RuntimeError('BAD_VIDEO_DURATION')
    return value


def sha_file(fd:int)->tuple[str,int]:
    os.lseek(fd,0,os.SEEK_SET)
    h=hashlib.sha256(); n=0
    while True:
        chunk=os.read(fd,1024*1024)
        if not chunk:break
        n+=len(chunk);h.update(chunk)
    os.lseek(fd,0,os.SEEK_SET)
    return h.hexdigest(),n


def read_index(guard,rel):
    parts=rel.split('/')
    parent=guard._open_parent(parts[:-1])
    try:
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
        try:
            info=os.fstat(fd)
            if not os.path.isfile(f'/proc/self/fd/{fd}') or info.st_dev!=guard._initial_dev or info.st_size>15000:
                raise RuntimeError('UNSAFE_INDEX')
            raw=os.read(fd,15001)
            obj=json.loads(raw)
            if obj.get('schema')!='meteormesh.event.review-index.v1' or obj.get('clip_generated') is not False:
                raise RuntimeError('INVALID_INDEX_SCHEMA')
            return obj
        finally:os.close(fd)
    finally:os.close(parent)


def pick(guard,camera,night):
    index_root=DATA/'event-review'
    if not index_root.is_dir() or index_root.is_symlink():
        raise RuntimeError('NO_REVIEW_INDEX_YET: run meteor-event-review.py once')
    days=[night] if night else sorted((x.name for x in index_root.iterdir() if x.is_dir() and not x.is_symlink() and DAY.fullmatch(x.name)),reverse=True)
    candidates=[]
    for d in days:
        if not DAY.fullmatch(d):raise RuntimeError('BAD_NIGHT_ID')
        folder=index_root/d
        if not folder.is_dir() or folder.is_symlink():continue
        for name in sorted(x.name for x in folder.iterdir() if x.is_file() and not x.is_symlink() and re.fullmatch(r'e_[a-f0-9]{24}\.json',x.name)):
            obj=read_index(guard,f'event-review/{d}/{name}')
            if obj.get('camera_id') not in CAMERAS or (camera and obj.get('camera_id')!=camera):continue
            win=obj.get('suggested_60s_review_window')
            if not isinstance(win,dict) or win.get('exact_pts_calibrated') is not False:continue
            sec=win.get('start_seconds')
            if not isinstance(sec,(int,float)) or isinstance(sec,bool) or not 0<=sec<3600:continue
            seg=obj.get('segment_id','')
            match=SEG.fullmatch(seg)
            if not match or obj.get('camera_id')!='cam-'+match.group(1):continue
            if not ID.fullmatch(obj.get('package_id','')) or name!=obj['package_id']+'.json':continue
            if obj.get('source_video_relative_path')!=obj['camera_id']+'/'+seg+'.mp4':continue
            if not HEX.fullmatch(obj.get('source_video_sha256_at_ingest','')):continue
            candidates.append((d,obj))
    if not candidates:raise RuntimeError('NO_TIMED_TEST_CANDIDATE_FOUND')
    # Prioritize Ruby & Surya while Viva is offline; only one selected sample.
    candidates.sort(key=lambda x:({'cam-ruby':0,'cam-surya':1,'cam-viva':2}[x[1]['camera_id']],x[0],x[1]['package_id']))
    for d,obj in candidates:
        output_dir=DATA/'event-clips'/d
        if (output_dir/(obj['package_id']+'.mp4')).exists() or (output_dir/(obj['package_id']+'.mp4.part')).exists():
            continue
        parent=guard._open_parent([obj['camera_id']])
        try:
            fd=os.open(obj['segment_id']+'.mp4',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
            try:
                info=os.fstat(fd)
                if not os.path.isfile(f'/proc/self/fd/{fd}') or info.st_dev!=guard._initial_dev:
                    os.close(fd);continue
                dur=duration_of(fd)
                start=obj['suggested_60s_review_window']['start_seconds']
                if start+60>dur-0.2:
                    os.close(fd);continue  # no unmarked truncated clip
                return d,obj,fd,dur
            except Exception:
                os.close(fd);raise
        finally:os.close(parent)
    raise RuntimeError('NO_FULL_60S_WINDOW_IN_AVAILABLE_SOURCE')


def produce(guard,day,index,video_fd,source_duration):
    camera=index['camera_id']; eid=index['package_id']
    h,n=sha_file(video_fd)
    if h!=index['source_video_sha256_at_ingest']:
        raise RuntimeError('SOURCE_SHA256_MISMATCH_NO_CLIP')
    source_start=index['suggested_60s_review_window']['start_seconds']
    guard.create_dir('event-clips');guard.create_dir('event-clips/'+day)
    parent=guard._open_parent(['event-clips',day]);partial=eid+'.mp4.part'; final=eid+'.mp4'; side=eid+'.json'
    try:
        for name in [partial,final,side]:
            try:
                os.stat(name,dir_fd=parent,follow_symlinks=False)
            except FileNotFoundError:pass
            else:raise RuntimeError('OUTPUT_ALREADY_EXISTS: '+name)
        guard.verify()
        fd=os.open(partial,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=parent)
        try:
            cmd=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-ss',str(source_start),
                 '-i',f'/proc/self/fd/{video_fd}', '-map','0:v:0','-an','-t','60',
                 '-c:v','copy','-movflags','+frag_keyframe+empty_moov+default_base_moof',
                 '-f','mp4','pipe:1']
            run=subprocess.run(cmd,stdout=fd,stderr=subprocess.PIPE,timeout=180,pass_fds=(video_fd,))
            if run.returncode:raise RuntimeError('FFMPEG_FAILED: '+run.stderr.decode('utf-8','replace')[:200])
            os.fsync(fd)
        finally:os.close(fd)
        guard.verify()
        clipfd=os.open(partial,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
        try:
            clipsha,clipsize=sha_file(clipfd)
            actual_duration=duration_of(clipfd)
        finally:os.close(clipfd)
        if not (55<=actual_duration<=62) or clipsize<1024:
            raise RuntimeError('CLIP_DURATION_OR_SIZE_INVALID: '+str(actual_duration))
        guard.verify()
        # link() is exclusive: never replaces another output; unlink only own temp.
        os.link(partial,final,src_dir_fd=parent,dst_dir_fd=parent,follow_symlinks=False)
        os.fsync(parent)
        os.unlink(partial,dir_fd=parent)
        receipt={'schema':'meteormesh.test-preview.v1','package_id':eid,'night_date_local':day,
                 'camera_id':camera,'source_segment_id':index['segment_id'],
                 'source_sha256_verified_now':h,'source_bytes_verified':n,
                 'start_seconds_estimated':source_start,'requested_duration_seconds':60,
                 'clip_duration_seconds_measured':round(actual_duration,3),
                 'clip_sha256':clipsha,'clip_bytes':clipsize,
                 'source_duration_seconds':round(source_duration,3),
                 'time_alignment':'APPROXIMATE_FRAME_DERIVED','test_only':True,
                 'confirmed_meteor':False,'original_deleted':False,'auto_archive':False}
        raw=(json.dumps(receipt,sort_keys=True,separators=(',',':'))+'\n').encode()
        guard.write_new(f'event-clips/{day}/{side}',[raw],expected_bytes=len(raw))
        guard.verify()
        print('TEST_CLIP: PASS')
        print('CAMERA:',camera,'DAY:',day)
        print('CLIP:',str(DATA/'event-clips'/day/final))
        print('DURATION:',round(actual_duration,2),'SECONDS; ORIGINALS: UNCHANGED; CLOUD: OFF')
        print('CLIP_SHA256:',clipsha)
    finally:os.close(parent)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--camera',choices=sorted(CAMERAS));ap.add_argument('--night',help='YYYYMMDD local night')
    args=ap.parse_args()
    if HUB.is_symlink() or not (HUB/'storage_guard.py').is_file():raise RuntimeError('RC14_GUARD_MISSING')
    sys.path.insert(0,str(HUB))
    from storage_guard import GuardedStorage,StoragePolicy
    with GuardedStorage(StoragePolicy()) as guard:
        night,obj,fd,duration=pick(guard,args.camera,args.night)
        try:produce(guard,night,obj,fd,duration)
        finally:os.close(fd)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('TEST_CLIP_STOPPED:',type(exc).__name__,str(exc)[:260],file=sys.stderr)
        sys.exit(2)
