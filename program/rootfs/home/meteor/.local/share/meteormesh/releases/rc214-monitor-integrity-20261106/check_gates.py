import ast, copy
from pathlib import Path
from datetime import datetime, timezone, timedelta, time as dt_time
tree=ast.parse((Path(__file__).parent/'monitor/meteor-monitor-server.py').read_text(encoding='utf-8'))
fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='natural_timer_gates')
ns=dict(datetime=datetime,timezone=timezone,timedelta=timedelta,dt_time=dt_time,TZ=timezone(timedelta(hours=3)))
exec(compile(ast.Module(body=[fn],type_ignores=[]),'isolated-gate','exec'),ns)
base=dict(timers=[dict(unit='meteormesh-predawn.timer',last_local='2026-11-06T05:30:23+03:00')],
 services=dict(predawn=dict(state='inactive',result='success'),morning=dict(state='inactive',result='success')),
 analysis_execution=dict(started_local='2026-11-06T05:30:27+03:00',state='COMPLETED',source=dict(night_id='20261105-night')),
 reports=dict(today_sync_reports=[dict(night_id='20261105-night',phase='sync',finished_local='2026-11-06T05:30:26+03:00')]),
 capture_night='20261105-night',now=datetime.fromisoformat('2026-11-06T12:00:00+03:00'),
 capture_segments=dict(night_id='20261105-night',state='READ_OK',analysis_queue=dict(WAITING=0,RUNNING=0,SUCCEEDED=360,FAILED=0,NO_JOB_SEGMENTS=0)),
 scope_matches_capture=True,report_matches_capture=True,carryover=dict(WAITING=99,FAILED=119),detector=dict(analysis_complete=True))
def check(name,change,gate,expected):
 x=copy.deepcopy(base);change(x);r=ns['natural_timer_gates'](**x)
 assert r[gate]['status']==expected,(name,r)
 print('PASS',name)
check('historical missing does not block current complete night',lambda x:None,'analysis_drain','PASS')
check('database unavailable never empty success',lambda x:x['capture_segments'].update(state='UNAVAILABLE',analysis_queue={}),'analysis_drain','BLOCKED')
check('malformed count',lambda x:x['capture_segments']['analysis_queue'].update(WAITING='bad'),'analysis_drain','BLOCKED')
check('boolean count',lambda x:x['capture_segments']['analysis_queue'].update(WAITING=False),'analysis_drain','BLOCKED')
check('new unknown job state',lambda x:x['capture_segments']['analysis_queue'].update(RETRY_PENDING=1),'analysis_drain','BLOCKED')
check('wrong queue scope',lambda x:x['capture_segments'].update(night_id='20261104-night'),'analysis_drain','BLOCKED')
check('old success counts cannot prove fresh analyzer start',lambda x:x.update(analysis_execution={}),'predawn','PARTIAL')
check('waiting jobs',lambda x:x['capture_segments']['analysis_queue'].update(WAITING=4),'analysis_drain','PARTIAL')
check('current failed jobs',lambda x:x['capture_segments']['analysis_queue'].update(FAILED=1),'analysis_drain','PARTIAL')
check('old report',lambda x:x.update(report_matches_capture=False),'analysis_drain','PARTIAL')
check('before natural start',lambda x:x.update(now=datetime.fromisoformat('2026-11-06T05:20:00+03:00')),'predawn','PARTIAL')
