"""Continue bounded retention on verified empty-source daytime rounds."""
import json,os,sqlite3,subprocess
from pathlib import Path
BASE=Path.home()/'.local/share/meteormesh/releases'
OLD=BASE/'rc210-backup-proof-20261105/ops/post-analysis-condition-gate.py'
def command(args):
 return subprocess.run(args,capture_output=True,text=True,timeout=30,check=False)
def empty_source_proven(result):
 if result.returncode!=1:return False
 lines=[x for x in result.stdout.splitlines() if x.startswith('ANALYSIS_QUEUE_NO_ACTIONABLE_SOURCE: ')]
 if len(lines)!=1:return False
 try:d=json.loads(lines[0].split(': ',1)[1])
 except (ValueError,TypeError):return False
 return type(d.get('source_ready_jobs')) is int and d['source_ready_jobs']==0 and isinstance(d.get('night_id'),str)
def cleanup_allowed():
 window=command(['/usr/bin/python3',str(BASE/'rc194-analysis-capacity-20261105/ops/meteor-analysis-window.py'),'--timezone','Europe/Istanbul','--start','05:30','--stop-before','18:45'])
 if window.returncode!=0 or 'ANALYSIS_WINDOW: ALLOW' not in window.stdout:return False
 for unit in ('meteormesh-hourly-pull.service','meteormesh-midnight.service','meteormesh-morning.service'):
  r=command(['systemctl','--user','show',unit,'-p','ActiveState','--value'])
  if r.returncode or r.stdout.strip() not in ('inactive','failed'):return False
 guard=command(['/usr/bin/python3',str(BASE/'rc158-20261103-analysis-rounds/ops/meteor-analysis-queue-guard.py')])
 if guard.returncode not in (0,1) or not guard.stdout.startswith(('ANALYZE_QUEUE_READY ','ANALYZE_QUEUE_EMPTY ')):return False
 source=command(['/usr/bin/python3',str(BASE/'rc188a-analysis-source-backup-20261105/ops/meteor-analysis-source-ready.py'),'--night-scope',str(Path.home()/'.local/share/meteormesh/staging/fullnight/night-scope-current.json'),'--hub',str(BASE/'rc211-no-ready-segments-20261106/MeteorHub')])
 if not empty_source_proven(source):return False
 db=sqlite3.connect('file:/srv/meteor-data/meteorhub.sqlite3?mode=ro',uri=True,timeout=3)
 try:
  db.execute('PRAGMA query_only=ON')
  return db.execute("SELECT COUNT(*) FROM jobs WHERE state='RUNNING'").fetchone()[0]==0
 finally:db.close()
def main():
 env=os.environ.copy();result=env.get('SERVICE_RESULT','')
 if result=='exec-condition':
  try:allowed=cleanup_allowed()
  except (OSError,ValueError,sqlite3.Error,subprocess.SubprocessError):allowed=False
  if allowed:
   print('RC216: VERIFIED_EMPTY_SOURCE_CLEANUP max_deletes=5',flush=True)
   # Only this child may enter the existing successful-run hook; all deletion
   # eligibility, backup, exclusive lock and camera-quiet checks remain there.
   env['SERVICE_RESULT']='success'
  else:print('RC216: CLEANUP_NOT_AUTHORIZED_BY_CURRENT_STATE',flush=True)
 subprocess.run(['/usr/bin/python3','-B',str(OLD)],env=env,check=False)
 return 0
if __name__=='__main__':raise SystemExit(main())
