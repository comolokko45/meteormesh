import hashlib,json,subprocess
from pathlib import Path
from datetime import datetime,timezone
root=Path(__file__).resolve().parent
def run(*args):return subprocess.check_output(args,text=True).strip()
unit='meteormesh-analyze.service'
previous=run('systemctl','--user','show',unit,'-p','ExecStopPost','--value')
if 'rc216-cleanup-continuation-20261106/rc216_cleanup_gate.py' not in previous:raise SystemExit('Unexpected active hook; refusing deployment')
override=Path.home()/'.config/systemd/user/meteormesh-analyze.service.d/~rc217-cleanup-capacity.conf'
if override.exists():raise SystemExit('Override already exists')
files=['after_analysis_with_backup.py','cleanup_gate.py','post-analysis-condition-gate.py']
for name in files:compile((root/name).read_text(encoding='utf-8'),name,'exec')
(root/'manifest.json').write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'previous_hook':previous,'override':str(override),'sha256':{n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in files},'rollback':'Remove only '+str(override)+' then systemctl --user daemon-reload; no analyzer restart'},indent=2),encoding='utf-8')
try:
 override.write_text('[Service]\nExecStopPost=\nExecStopPost=/usr/bin/python3 -B '+str(root/'cleanup_gate.py')+'\n',encoding='utf-8')
 run('systemctl','--user','daemon-reload')
 actual=run('systemctl','--user','show',unit,'-p','ExecStopPost','--value')
 if str(root/'cleanup_gate.py') not in actual:raise RuntimeError('Override not effective')
 print(actual)
 print('DEPLOYED_WITHOUT_RESTART')
except Exception:
 override.unlink(missing_ok=True);run('systemctl','--user','daemon-reload');raise
