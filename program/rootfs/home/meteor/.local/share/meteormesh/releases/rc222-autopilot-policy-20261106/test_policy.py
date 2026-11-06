import copy,datetime as dt
from autopilot_policy import evaluate
now=dt.datetime.fromisoformat('2026-11-06T22:15:00+03:00')
base={'night_status':{'night_id':'20261106-night','phone_purge':{'deleted_by_device':{'cam-surya':10}},'detector_summary':{'jobs_in_night':{'WAITING':20,'RUNNING':0,'SUCCEEDED':0,'FAILED':0}}},'units':{'meteormesh-hourly-pull.timer':{'state':'active','enabled':'enabled'}},'phone_heartbeat':{'state':'READ_OK','devices':{'surya':{'storage':{'free_bytes':33*1024**3,'protected_reserve_bytes':32*1024**3,'last_sample_utc':now.isoformat()}}}}}
one,changed=evaluate(base,{},now)
assert any(x['reason']=='FIVE_MINUTE_TRANSFER_OWNS_RETRY' for x in one['decisions'])
assert any(x['state']=='EXPECTED_WAIT' and x['target']=='analysis' for x in one['decisions'])
two,changed=evaluate(base,one,now);assert not changed
late=copy.deepcopy(base);late['phone_heartbeat']['devices']['surya']['storage']['last_sample_utc']='2026-11-06T20:00:00+03:00'
assert any(x['reason']=='STALE_OR_MISSING_STORAGE_EVIDENCE' for x in evaluate(late,one,now)[0]['decisions'])
up=copy.deepcopy(base);up['phone_heartbeat']['devices']['surya']['storage'].update(free_bytes=34*1024**3,last_sample_utc=(now+dt.timedelta(minutes=5)).isoformat());up['night_status']['phone_purge']['deleted_by_device']['cam-surya']=11
assert evaluate(up,one,now+dt.timedelta(minutes=5))[0]['storage']['surya']['verification']=='PURGE_AND_SPACE_INCREASE_OBSERVED'
bad=copy.deepcopy(base);bad['units']={}
assert any(x['state']=='SHADOW_RETRY' for x in evaluate(bad,one,now)[0]['decisions'])
noqueue=copy.deepcopy(base);noqueue['night_status']['detector_summary']={}
assert any(x['reason']=='SCOPED_QUEUE_UNVERIFIED' for x in evaluate(noqueue,one,now)[0]['decisions'])
assert not any(x['executed'] for x in one['decisions'])
assert one['mutations_enabled'] is False
morning=copy.deepcopy(base);morning['night_status']['detector_summary'].update(analysis_complete=True,jobs_in_night={'WAITING':0,'RUNNING':0,'SUCCEEDED':360,'FAILED':0})
qualified=evaluate(morning,one,dt.datetime.fromisoformat('2026-11-07T16:00:00+03:00'))[0]
assert qualified['qualification']=='NATURAL_NIGHT_AND_MORNING_OBSERVED_REVIEW_READY' and qualified['mutations_enabled'] is False
print('PASS timer ownership, night window, stale evidence, purge/space correlation, retry shadow, queue fail-closed, dedup, no mutations')
