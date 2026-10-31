#!/usr/bin/env python3
"""Create one verified, TEST-only event package manifest from a finished clip.

No raw-video modification, phone access, cloud upload or deletion. Uses RC1.4
GuardedStorage to fail closed if the required HDD is not correctly mounted.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path('/srv/meteor-data')
HUB = Path.home()/'.local/share/meteormesh/releases/rc14-20261031-split/MeteorHub'


def read_guarded(guard, relative, limit=None, hash_only=False):
    parts = relative.split('/')
    if not all(re.fullmatch(r'[a-zA-Z0-9_.-]+', x) and x not in ('.','..') for x in parts):
        raise RuntimeError('BAD_FILE_PATH')
    guard.verify()
    parent = guard._open_parent(parts[:-1], create=False)
    try:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try:
            s = os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_dev != guard._initial_dev:
                raise RuntimeError('FILE_NOT_ON_VERIFIED_HDD')
            if limit is not None and s.st_size > limit:
                raise RuntimeError('FILE_TOO_LARGE')
            h = hashlib.sha256()
            contents = bytearray() if not hash_only else None
            count=0
            while True:
                b=os.read(fd,1024*1024)
                if not b: break
                count+=len(b); h.update(b)
                if contents is not None: contents.extend(b)
            if count != s.st_size: raise RuntimeError('FILE_SIZE_CHANGED')
            guard.verify()
            return (bytes(contents) if contents is not None else None),h.hexdigest(),count
        finally: os.close(fd)
    finally: os.close(parent)


def run(day, package_id):
    sys.path.insert(0,str(HUB))
    from storage_guard import GuardedStorage, StoragePolicy
    with GuardedStorage(StoragePolicy()) as g:
        idx_rel=f'event-review/{day}/{package_id}.json'
        rec_rel=f'event-clips/{day}/{package_id}.json'
        clip_rel=f'event-clips/{day}/{package_id}.mp4'
        idx_raw,idx_sha,_=read_guarded(g,idx_rel,20000)
        rec_raw,rec_sha,_=read_guarded(g,rec_rel,20000)
        idx=json.loads(idx_raw); rec=json.loads(rec_raw)
        if idx.get('schema') != 'meteormesh.event.review-index.v1' or rec.get('schema')!='meteormesh.test-preview.v1':
            raise RuntimeError('UNEXPECTED_SCHEMA')
        for d in (idx,rec):
            if d.get('package_id')!=package_id or d.get('night_date_local')!=day:
                raise RuntimeError('PACKAGE_ID_OR_DAY_MISMATCH')
        if idx.get('camera_id')!=rec.get('camera_id') or idx.get('segment_id')!=rec.get('source_segment_id'):
            raise RuntimeError('CAMERA_OR_SEGMENT_MISMATCH')
        if idx.get('source_video_sha256_at_ingest') != rec.get('source_sha256_verified_now'):
            raise RuntimeError('SOURCE_SHA_MISMATCH')
        if rec.get('test_only') is not True or rec.get('confirmed_meteor') is not False:
            raise RuntimeError('NOT_TEST_PREVIEW')
        _,clipsha,clipbytes=read_guarded(g,clip_rel,hash_only=True)
        if clipsha!=rec.get('clip_sha256') or clipbytes!=rec.get('clip_bytes'):
            raise RuntimeError('CLIP_HASH_OR_SIZE_MISMATCH')
        receipt={
          'schema':'meteormesh.test-event-package.v1', 'package_id':package_id,
          'night_date_local':day,'camera_id':idx['camera_id'],
          'segment_id':idx['segment_id'],'source_video_sha256':idx['source_video_sha256_at_ingest'],
          'detector_result_sha256':idx['detector_result_sha256_verified'],
          'index_path':idx_rel,'index_sha256':idx_sha,
          'clip_receipt_path':rec_rel,'clip_receipt_sha256':rec_sha,
          'clip_path':clip_rel,'clip_sha256':clipsha,'clip_bytes':clipbytes,
          'candidate_unreviewed':True,'test_only':True,'confirmed_meteor':False,
          'cloud_uploaded':False,'originals_deleted':False,'automatic_delete_allowed':False
        }
        payload=(json.dumps(receipt,sort_keys=True,separators=(',',':'))+'\n').encode()
        relative=f'event-packages/{day}/{package_id}.json'
        g.create_dir('event-packages'); g.create_dir(f'event-packages/{day}')
        try:
            g.write_new(relative,[payload],expected_bytes=len(payload))
            result='CREATED'
        except Exception as e:
            if type(e).__name__!='StorageDenied' or str(e)!='EXCLUSIVE_CREATE_FAILED':raise
            existing,_,_=read_guarded(g,relative,10000)
            if existing!=payload: raise RuntimeError('EXISTING_PACKAGE_CONFLICT')
            result='EXISTS_VERIFIED'
    print('TEST_EVENT_PACKAGE: PASS')
    print('STATE:',result)
    print('CAMERA:',idx['camera_id'])
    print('PACKAGE:',ROOT/relative)
    print('VIDEO_SHA256:',clipsha)
    print('SOURCE_UNCHANGED: YES; CLOUD: OFF; AUTO_DELETE: OFF')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--day',default='20261030')
    p.add_argument('--id',default='e_030f8f2d59d443155a4219de')
    a=p.parse_args()
    if not re.fullmatch(r'20[0-9]{6}',a.day) or not re.fullmatch(r'e_[a-f0-9]{24}',a.id):
        raise RuntimeError('INVALID_DAY_OR_ID')
    run(a.day,a.id)

if __name__=='__main__':
    try: main()
    except Exception as e:
        print('TEST_EVENT_PACKAGE_STOPPED:',type(e).__name__,str(e)[:220],file=sys.stderr)
        sys.exit(2)
