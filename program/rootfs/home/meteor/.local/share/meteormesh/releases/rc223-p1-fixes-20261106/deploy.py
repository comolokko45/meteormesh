import hashlib,json,subprocess
from pathlib import Path
root=Path(__file__).resolve().parent
def run(*a):return subprocess.check_output(a,text=True).strip()
for unit in ('meteormesh-status.service','meteormesh-analyze.service'):
 if run('systemctl','--user','show',unit,'-p','ActiveState','--value') not in ('inactive','failed'):raise RuntimeError('Busy '+unit)
old=run('systemctl','--user','show','meteormesh-status.service','-p','ExecStartPost','--value')
if 'rc222-autopilot-policy-20261106/meteor-ops-journal.py' not in old:raise RuntimeError('Unexpected journal')
ops=Path.home()/'.config/systemd/user/meteormesh-status.service.d/~rc223-p1.conf'
cleanup=Path.home()/'.config/systemd/user/meteormesh-analyze.service.d/~rc223-p1.conf'
if ops.exists() or cleanup.exists():raise RuntimeError('Already deployed')
for p in root.glob('*.py'):compile(p.read_text(encoding='utf-8'),str(p),'exec')
(root/'manifest.json').write_text(json.dumps({'previous_status_hooks':old,'overrides':[str(ops),str(cleanup)],'hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*.py')}},indent=2))
try:
 ops.write_text('[Service]\nExecStartPost=\nExecStartPost=-/usr/bin/python3 -B '+str(root/'meteor-heartbeat-watch.py')+' --commit --quiet --allow-safe-listener-recovery\nExecStartPost=-/usr/bin/python3 -B '+str(root/'meteor-ops-journal.py')+'\n')
 cleanup.write_text('[Service]\nExecStopPost=\nExecStopPost=/usr/bin/python3 -B '+str(root/'cleanup_gate.py')+'\n')
 run('systemctl','--user','daemon-reload')
 print('DEPLOYED RC223: no service restart, no deletion, no phone update')
except Exception:
 ops.unlink(missing_ok=True);cleanup.unlink(missing_ok=True);run('systemctl','--user','daemon-reload');raise
