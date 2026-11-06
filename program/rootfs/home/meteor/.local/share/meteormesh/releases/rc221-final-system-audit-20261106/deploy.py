import hashlib,json,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parent
def run(*args):return subprocess.check_output(args,text=True).strip()
before=run('systemctl','--user','show','meteormesh-monitor.service','-p','ExecStart','--value')
assert 'rc218-mobile-management-20261106/manage_server.py' in before
assert run('systemctl','--user','show','meteormesh-capture.service','-p','ActiveState','--value')=='inactive'
requests=Path.home()/'.local/state/meteormesh/mobile-control/requests.json'
assert not any(r.get('status')=='EXECUTING' for r in json.loads(requests.read_text()))
monitor=Path.home()/'.config/systemd/user/meteormesh-monitor.service.d/96-rc221-invocation-proof.conf'
capture=Path.home()/'.config/systemd/user/meteormesh-capture.service.d/~rc221-storage-priority.conf'
assert not monitor.exists() and not capture.exists()
(root/'manifest.json').write_text(json.dumps({'previous_monitor':before,'overrides':[str(monitor),str(capture)],'sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.suffix in ('.py','.html')}},indent=2))
try:
 monitor.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B '+str(root/'manage_server.py')+'\n')
 capture.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B '+str(root/'capture_control.py')+'\n')
 run('systemctl','--user','daemon-reload');run('systemctl','--user','restart','meteormesh-monitor.service')
 time.sleep(1)
 assert run('curl','-fsS','http://127.0.0.1:8765/healthz')=='ok'
 print('RC221 DEPLOYED; monitor restarted only; capture takes effect next natural tick')
except Exception:
 monitor.unlink(missing_ok=True);capture.unlink(missing_ok=True);run('systemctl','--user','daemon-reload');run('systemctl','--user','restart','meteormesh-monitor.service');raise
