import sqlite3,json,statistics
from pathlib import Path
db=sqlite3.connect('file:/srv/meteor-data/meteorhub.sqlite3?mode=ro',uri=True);db.execute('PRAGMA query_only=ON')
rows=db.execute('select device_id,segment_id,sha256 from segments').fetchall();db.close()
out={}
for cam in ('cam-ruby','cam-surya','cam-viva'):
 items=[];bad=0
 for device,seg,sha in rows:
  if device!=cam:continue
  stamp=seg.split('_')[1]
  if not '20261105T160000Z'<=stamp<'20261106T020000Z':continue
  try:
   d=json.loads((Path('/srv/meteor-data')/cam/(seg+'.json')).read_text())
   if d.get('sha256')!=sha:bad+=1;continue
   items.append(d)
  except (OSError,ValueError):bad+=1
 items.sort(key=lambda d:d['media_presentation_start_us'])
 gaps=[(b['media_presentation_start_us']-a['media_presentation_end_us'])/1000 for a,b in zip(items,items[1:])]
 sensor=[(b['first_exposure_sensor_ns']-a['last_exposure_sensor_ns'])/1e6 for a,b in zip(items,items[1:])]
 out[cam]={'segments':len(items),'missing_or_mismatched_sidecars':bad,'boundaries':len(gaps),'pts_gap_ms_min':min(gaps) if gaps else None,'pts_gap_ms_max':max(gaps) if gaps else None,'pts_gap_ms_median':statistics.median(gaps) if gaps else None,'pts_nonpositive':sum(x<=0 for x in gaps),'pts_over100ms':sum(x>100 for x in gaps),'sensor_gap_ms_max':max(sensor) if sensor else None,'boundary_verified_flags':sorted(set(d.get('segment_boundary_verified') for d in items)),'timestamp_accuracy':sorted(set(d.get('timestamp_accuracy') for d in items))}
Path('/tmp/rc217-boundaries.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))
