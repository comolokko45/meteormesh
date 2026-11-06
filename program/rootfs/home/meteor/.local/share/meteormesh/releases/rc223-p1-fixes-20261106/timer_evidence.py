"""Read actual systemd timer/service identity; never infer a trigger from queue completion."""
import datetime as dt,os,re,subprocess
def props(unit,fields):
 r=subprocess.run(['systemctl','--user','show',unit,*['-p'+f for f in fields]],capture_output=True,text=True,timeout=4,check=True,env={**os.environ,'TZ':'UTC','LC_ALL':'C'})
 return dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
def timestamp(raw):
 m=re.search(r'(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) UTC',raw or '')
 if not m:return None
 return dt.datetime.fromisoformat(m[1]+'T'+m[2]+'+00:00').isoformat(timespec='seconds')
def collect(night):
 try:
  t=props('meteormesh-predawn.timer',['LastTriggerUSec'])
  s=props('meteormesh-predawn.service',['ExecMainStartTimestamp','Result','InvocationID'])
  return {'source':'SYSTEMD_PROPERTIES','night_id':night,'trigger_utc':timestamp(t.get('LastTriggerUSec')),'service_started_utc':timestamp(s.get('ExecMainStartTimestamp')),'service_result':s.get('Result'),'invocation_id':s.get('InvocationID'),'collected_utc':dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}
 except (OSError,ValueError,subprocess.SubprocessError):return {'source':'UNAVAILABLE'}
