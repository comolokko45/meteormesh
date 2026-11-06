"""Pure shadow decisions. No systemctl, phone traffic, SQLite writes or retries."""
import datetime as dt,hashlib,json
from zoneinfo import ZoneInfo
TZ=ZoneInfo('Europe/Istanbul')
def instant(value):
 try:
  result=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
  return result if result.tzinfo else None
 except (TypeError,AttributeError,ValueError):return None
def evaluate(summary,previous,now):
 if not isinstance(summary,dict):raise ValueError('INVALID_SUMMARY')
 local=now.astimezone(TZ);night=summary.get('night_status',{});units=summary.get('units',{})
 if not isinstance(night,dict):night={}
 if not isinstance(units,dict):units={}
 old=previous if isinstance(previous,dict) else {}
 decisions=[];storage={}
 phone=summary.get('phone_heartbeat',{})
 if not isinstance(phone,dict):phone={}
 rows=phone.get('devices',{}) if phone.get('state')=='READ_OK' else {}
 codes=summary.get('assessment',{}).get('codes',[])
 def add(target,state,reason,action='NONE'):
  decisions.append({'target':target,'state':state,'reason':reason,'proposed_action':action,'executed':False,'retry_limit':2,'cooldown_seconds':900})
 for name in ('surya','ruby','viva'):
  row=rows.get(name,{})
  info=row.get('storage',{});sample=instant(info.get('last_sample_utc'))
  age=(now-sample).total_seconds() if sample else None
  free=info.get('free_bytes');reserve=info.get('protected_reserve_bytes')
  fresh=age is not None and 0<=age<=600 and type(free) is int and type(reserve) is int and reserve>0 and free>=0
  prior=old.get('storage',{}).get(name,{})
  purge=night.get('phone_purge',{}).get('deleted_by_device',{}).get('cam-'+name)
  status='UNVERIFIED'
  if fresh:
   status='RESERVE_OK' if free>=reserve else 'BELOW_RESERVE'
   delta=None
   if (type(prior.get('free')) is int and prior.get('sample')!=info['last_sample_utc'] and
       prior.get('night')==night.get('night_id') and type(purge) is int and type(prior.get('purge')) is int and purge>prior['purge']):
    delta=free-prior['free'];status='PURGE_AND_SPACE_INCREASE_OBSERVED' if delta>0 else 'PURGE_SEEN_SPACE_NOT_INCREASED'
   storage[name]={'free':free,'reserve':reserve,'sample':info['last_sample_utc'],'purge':purge,'night':night.get('night_id'),'verification':status,'delta':delta}
   if free-reserve<2*1024**3 or 'STORAGE_LOW:'+name in codes:
    pull=units.get('meteormesh-hourly-pull.service',{});timer=units.get('meteormesh-hourly-pull.timer',{})
    if pull.get('state') in ('active','activating','deactivating'):add(name,'DELEGATED','TRANSFER_RUNNING')
    elif timer.get('state')=='active' and timer.get('enabled')=='enabled':add(name,'DELEGATED','FIVE_MINUTE_TRANSFER_OWNS_RETRY')
    else:add(name,'SHADOW_RETRY','TRANSFER_TIMER_NOT_READY','REQUEST_EXISTING_SYNC')
  else:add(name,'BLOCKED','STALE_OR_MISSING_STORAGE_EVIDENCE')
  progress=summary.get('capture_progress',{}).get(name,{})
  if progress.get('state') in ('STALLED_NEEDS_REARM','STALLED_NOT_RECORDING'):
   add(name,'NEEDS_REARM','CAMERA_REQUIRES_OPERATOR')
  elif 'CAPTURE_STALLED:'+name in codes:add(name,'NEEDS_REVIEW','MP4_NOT_ADVANCING_NO_BLIND_RESTART')
  if 'API_DOWN:'+name in codes:add(name,'DELEGATED','EXISTING_CONTROL_LISTENER_GATES_OWN_RECOVERY')
 queue=night.get('detector_summary',{}).get('jobs_in_night',{})
 count=lambda key:queue.get(key,0) if type(queue.get(key,0)) is int else 0
 previous_queue=old.get('queue',{})
 same_night=old.get('night_id')==night.get('night_id')
 unchanged=same_night and previous_queue==queue
 since=old.get('queue_unchanged_since') if unchanged else now.isoformat(timespec='seconds')
 start=instant(since);elapsed=(now-start).total_seconds() if start else 0
 window=(local.hour>5 or (local.hour==5 and local.minute>=30)) and (local.hour<18 or (local.hour==18 and local.minute<45))
 if not isinstance(queue,dict) or not all(type(queue.get(k)) is int and queue[k]>=0 for k in ('WAITING','RUNNING','SUCCEEDED','FAILED')):add('analysis','BLOCKED','SCOPED_QUEUE_UNVERIFIED')
 elif not window:add('analysis','EXPECTED_WAIT','OUTSIDE_ANALYSIS_WINDOW')
 elif count('FAILED')>0:add('analysis','NEEDS_REVIEW','FAILED_JOBS_REQUIRE_CAUSE_NOT_BLIND_RETRY')
 elif count('RUNNING')>0:add('analysis','DELEGATED','EXISTING_WORKER_RUNNING')
 elif count('WAITING')>0:
  if elapsed>=1800:add('analysis','SHADOW_RETRY','QUEUE_UNCHANGED_30_MINUTES','REQUEST_EXISTING_ANALYSIS')
  else:add('analysis','DELEGATED','EXISTING_ANALYSIS_TIMER')
 else:add('analysis','IDLE','NO_WAITING_JOB_IN_REPORTED_SCOPE')
 started=old.get('shadow_started_utc') or now.isoformat(timespec='seconds')
 first_night=old.get('shadow_night_id') or night.get('night_id')
 proof='NATURAL_NIGHT_AND_MORNING_PENDING'
 try:
  day=dt.datetime.strptime(first_night,'%Y%m%d-night').date()
  morning=dt.datetime.combine(day+dt.timedelta(days=1),dt.time(5,30),TZ)
  began=instant(started)
  if (began is not None and began<morning-dt.timedelta(hours=1) and now>=morning
      and night.get('night_id')==first_night
      and night.get('detector_summary',{}).get('analysis_complete') is True):
   proof='NATURAL_NIGHT_AND_MORNING_OBSERVED_REVIEW_READY'
 except (ValueError,TypeError):pass
 result={'mode':'SHADOW','mutations_enabled':False,'shadow_started_utc':started,'shadow_night_id':first_night,'observed_utc':now.isoformat(timespec='seconds'),'night_id':night.get('night_id'),'decisions':decisions[:12],'storage':storage,'queue':queue,'queue_unchanged_since':since,'qualification':proof,'external_notification':'LOCAL_AND_APK_ONLY_BY_USER_CHOICE'}
 # Changing numeric samples must not create repeated decision notifications.
 fingerprint=hashlib.sha256(json.dumps(result['decisions'],sort_keys=True).encode()).hexdigest()[:16]
 result['decision_fingerprint']=fingerprint
 return result,fingerprint!=old.get('decision_fingerprint')
