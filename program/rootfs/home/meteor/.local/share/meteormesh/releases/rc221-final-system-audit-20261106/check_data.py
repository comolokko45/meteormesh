import json,sqlite3
from pathlib import Path
d=sqlite3.connect('file:/srv/meteor-data/meteorhub.sqlite3?mode=ro',uri=True);d.row_factory=sqlite3.Row;d.execute('PRAGMA query_only=ON')
out={'quick_check':d.execute('PRAGMA quick_check').fetchone()[0]}
out['retirement_mismatch_count']=d.execute("SELECT COUNT(*) FROM source_dispositions x LEFT JOIN jobs j ON j.id=x.analysis_job_id WHERE j.id IS NULL OR j.state!='SUCCEEDED' OR j.device_id!=x.device_id OR j.segment_id!=x.segment_id OR j.video_sha256!=x.sha256").fetchone()[0]
out['retired_but_present']=sum((Path('/srv/meteor-data')/r['device_id']/(r['segment_id']+'.mp4')).exists() for r in d.execute("SELECT * FROM source_dispositions WHERE disposition='NO_CANDIDATE_DELETED'"))
out['retirement_counts']=dict(d.execute('SELECT disposition,COUNT(*) FROM source_dispositions GROUP BY disposition').fetchall())
out['current_night']=dict(d.execute("SELECT state,COUNT(*) FROM jobs WHERE substr(segment_id,instr(segment_id,'_')+1,16)>=? AND substr(segment_id,instr(segment_id,'_')+1,16)<? GROUP BY state",('20261106T160000Z','20261107T020000Z')).fetchall())
out['prior_night']=dict(d.execute("SELECT state,COUNT(*) FROM jobs WHERE substr(segment_id,instr(segment_id,'_')+1,16)>=? AND substr(segment_id,instr(segment_id,'_')+1,16)<? GROUP BY state",('20261105T160000Z','20261106T020000Z')).fetchall())
out['parts']=len(list(Path('/srv/meteor-data').glob('cam-*/*.part')))
print(json.dumps(out,indent=2))
