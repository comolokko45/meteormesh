import hashlib,json,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parent
def run(*a):return subprocess.check_output(a,text=True).strip()
assert run('systemctl','--user','show','meteormesh-status.service','-p','ActiveState','--value')=='inactive'
assert 'rc221-final-system-audit' in run('systemctl','--user','show','meteormesh-monitor.service','-p','ExecStart','--value')
requests=Path.home()/'.local/state/meteormesh/mobile-control/requests.json'
assert not any(x.get('status')=='EXECUTING' for x in json.loads(requests.read_text()))
snapshot=Path.home()/'.local/state/meteormesh/ops-alert-state-v1.json'
(root/'alert-state-before.json').write_bytes(snapshot.read_bytes());(root/'alert-state-before.json').chmod(0o600)
monitor=Path.home()/'.config/systemd/user/meteormesh-monitor.service.d/97-rc222-policy-view.conf'
ops=Path.home()/'.config/systemd/user/meteormesh-status.service.d/~rc222-policy.conf'
assert not monitor.exists() and not ops.exists()
try:
 monitor.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B '+str(root/'manage_server.py')+'\n')
 ops.write_text('[Service]\nExecStartPost=\nExecStartPost=-/usr/bin/python3 -B /home/meteor/.local/lib/meteormesh/meteor-heartbeat-watch.py --commit --quiet --allow-safe-listener-recovery\nExecStartPost=-/usr/bin/python3 -B '+str(root/'meteor-ops-journal.py')+'\n')
 run('systemctl','--user','daemon-reload');run('systemctl','--user','restart','meteormesh-monitor.service');time.sleep(1)
 assert run('curl','-fsS','http://127.0.0.1:8765/healthz')=='ok'
 (root/'manifest.json').write_text(json.dumps({'sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.suffix in ('.py','.html')},'overrides':[str(monitor),str(ops)]},indent=2))
 print('DEPLOYED RC222 SHADOW; only monitor restarted')
except Exception:
 monitor.unlink(missing_ok=True);ops.unlink(missing_ok=True);run('systemctl','--user','daemon-reload');run('systemctl','--user','restart','meteormesh-monitor.service');raise
