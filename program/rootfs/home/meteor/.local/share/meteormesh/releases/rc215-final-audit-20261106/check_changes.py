import ast,json,re,sqlite3,tempfile
from pathlib import Path
from datetime import datetime,timezone,timedelta
tree=ast.parse((Path(__file__).parent/'monitor/meteor-monitor-server.py').read_text(encoding='utf-8'))
names={'current_segments','_segment_start','read_retention_policy','retention_acceptance','_parse_utc'}
ns=dict(Path=Path,json=json,re=re,sqlite3=sqlite3,datetime=datetime,timezone=timezone,timedelta=timedelta,iso_local=lambda x:x.isoformat() if hasattr(x,'isoformat') else x)
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'isolated','exec'),ns)
with tempfile.TemporaryDirectory() as td:
 db=Path(td)/'db';c=sqlite3.connect(db)
 c.executescript('CREATE TABLE segments(device_id,segment_id,size_bytes,state,verified_utc,ffprobe_json); CREATE TABLE jobs(device_id,segment_id,state);')
 start=datetime(2026,11,5,16,tzinfo=timezone.utc);end=start+timedelta(hours=10)
 c.execute('INSERT INTO segments VALUES(?,?,?,?,?,?)',('cam-ruby','ruby_20261105T160000Z_x',10,'INGEST_VERIFIED',start.isoformat(),json.dumps({'format':{'duration':'300'}})));c.commit();c.close()
 ns['DB']=db
 def window(observed):return dict(night_id='20261105-night',start_utc=start,end_utc=end,start_local=start,end_local=end,observed_local=observed)
 ns['capture_window']=lambda now:window(end+timedelta(hours=6))
 r=ns['current_segments']();g=r['gaps']
 assert next(x for x in g if x['device_id']=='cam-ruby')['missing_seconds']==35700
 print('PASS closed-night missing tail retained and capped at window end')
 assert next(x for x in g if x['device_id']=='cam-surya')['missing_seconds']==36000
 print('PASS empty phone coverage reported')
 ns['capture_window']=lambda now:window(start+timedelta(seconds=400))
 assert not any(x['device_id']=='cam-ruby' for x in ns['current_segments']()['gaps'])
 print('PASS in-progress segment within grace not missing')
 marker=Path(td)/'policy';ns['RETENTION_POLICY']=marker
 marker.write_text(json.dumps({'schema':'meteormesh.negative-retention-activation.v1','auto_delete_enabled':True}))
 live=ns['read_retention_policy']();assert live['state']=='ENABLED_EXECUTION_GATED' and live['approval_required_before_enabling'] is False
 print('PASS live enabled policy overrides historical disabled label')
 out=ns['retention_acceptance']({'auto_delete_enabled':False,'auto_delete_enabled_utc':start.isoformat(),'last_deleted_utc':end.isoformat()},{'NO_CANDIDATE_DELETED':76})
 assert out['operations_status']=='BLOCKED'
 print('PASS previous deletion receipt cannot pass disabled policy')
 marker.write_text('{}');assert ns['read_retention_policy']()['auto_delete_enabled'] is False
 print('PASS invalid policy stays fail closed')
