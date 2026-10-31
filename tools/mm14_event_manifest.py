#!/usr/bin/env python3
"""Read-only single/multiple-camera candidate-event manifest, no archiving or deletion."""
import hashlib, importlib.util, json, os, sqlite3, sys
from pathlib import Path

REPORTER=Path.home()/'.local/lib/meteormesh/meteor-night-snapshot.py'

def main():
    if not REPORTER.is_file() or REPORTER.is_symlink():
        raise RuntimeError('SNAPSHOT_REPORTER_NOT_INSTALLED')
    spec=importlib.util.spec_from_file_location('mm_night_snapshot', REPORTER)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    mod.verify_mount(mod.DEFAULT_ROOT)
    scope=mod.safe_scope(mod.DEFAULT_SCOPE)
    dbfile=mod.DEFAULT_ROOT/'meteorhub.sqlite3'
    if dbfile.is_symlink() or not dbfile.is_file(): raise RuntimeError('DATABASE_NOT_FOUND')
    events=[]; errors=[]; processed=0
    with sqlite3.connect(dbfile.as_uri()+'?mode=ro',uri=True,timeout=10) as db:
        db.execute('PRAGMA query_only=ON')
        for jid,dev,seg,digest,result_path,result_sha in db.execute('''
            SELECT j.id,j.device_id,j.segment_id,j.video_sha256,r.result_path,r.result_sha256
            FROM jobs j JOIN detector_runs r ON r.job_id=j.id
            WHERE j.state='SUCCEEDED' ORDER BY j.id'''):
            if not mod.is_current(scope,dev,seg,digest): continue
            processed+=1
            try:
                if not isinstance(result_path,str) or not isinstance(result_sha,str) or not mod.HEX.fullmatch(result_sha):
                    raise ValueError('INVALID_RESULT_METADATA')
                p=Path(result_path);rp=p.resolve(strict=True)
                root=(mod.DEFAULT_ROOT/'detector-results').resolve(strict=True)
                if p.is_symlink() or not rp.is_relative_to(root) or not rp.is_file():
                    raise ValueError('RESULT_OUTSIDE_DATA_ROOT')
                if rp.stat().st_size>20*1024*1024: raise ValueError('RESULT_TOO_LARGE')
                raw=rp.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=result_sha: raise ValueError('RESULT_HASH_MISMATCH')
                obj=json.loads(raw)
                if not isinstance(obj,dict) or not isinstance(obj.get('results'),list):
                    raise ValueError('RESULT_SCHEMA_INVALID')
                for item_index,entry in enumerate(obj['results']):
                    if not isinstance(entry,dict) or not isinstance(entry.get('target'),list): continue
                    for candidate_index,t in enumerate(entry['target']):
                        if not isinstance(t,dict): continue
                        events.append({'candidate_id':f'{dev}:{seg}:{jid}:{item_index}:{candidate_index}',
                                       'device_id':dev,'segment_id':seg,'video_sha256':digest,
                                       'detector_job_id':jid,'detector_result_sha256':result_sha,
                                       'result_file':str(rp),'result_index':item_index,
                                       'target_index':candidate_index,'review_status':'UNREVIEWED'})
            except (OSError,ValueError,RuntimeError,json.JSONDecodeError) as e:
                errors.append({'job':jid,'device':dev,'error':type(e).__name__+': '+str(e)[:80]})
    document={'schema':'meteormesh.event-candidate-manifest.v1','night_id':scope['night_id'],
              'scope_start_utc_ms':scope['start_utc_ms'],'scope_end_utc_ms':scope['end_utc_ms'],
              'processed_successful_jobs':processed,'candidate_count':len(events),
              'status':'REVIEW_REQUIRED' if errors else 'CANDIDATES_UNREVIEWED',
              'confirmed_meteors':False,'video_copied':False,'archive_created':False,
              'auto_delete':False,'errors':errors[:30],'candidates':events}
    print('EVENT_MANIFEST: '+json.dumps(document,ensure_ascii=False,separators=(',',':')))
    return 0 if not errors else 2

if __name__=='__main__':
    try: sys.exit(main())
    except (Exception) as e:
        print('EVENT_MANIFEST_UNAVAILABLE:',type(e).__name__,str(e)[:160],file=sys.stderr)
        sys.exit(2)
