import hashlib,json,os,secrets,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parent
def run(*args):return subprocess.check_output(args,text=True).strip()
previous=run('systemctl','--user','show','meteormesh-monitor.service','-p','ExecStart','--value')
if 'rc217-cleanup-capacity-20261106/monitor/meteor-monitor-server.py' not in previous:raise SystemExit('Unexpected monitor release')
if run('systemctl','--user','show','meteormesh-capture.service','-p','ActiveState','--value')!='inactive':raise SystemExit('Capture tick busy; defer deployment')
original=Path.home()/'.local/lib/meteormesh/meteor-capture-loop.py'
assert original.read_bytes()==(root/'capture_base.py').read_bytes(),'Capture source changed'
for p in root.glob('*.py'):compile(p.read_text(encoding='utf-8'),str(p),'exec')
key=Path.home()/'.config/meteormesh/mobile-control-key.sha256'
if key.exists():raise SystemExit('Existing key: no replacement')
secret=secrets.token_urlsafe(32)
for path,text in [(key,hashlib.sha256(secret.encode()).hexdigest()),(root/'YONETIM_ANAHTARI.txt',secret)]:
 fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w') as f:f.write(text+'\n')
monitor=Path.home()/'.config/systemd/user/meteormesh-monitor.service.d/95-rc218-management.conf'
capture=Path.home()/'.config/systemd/user/meteormesh-capture.service.d/~rc218-manual-intent.conf'
assert not monitor.exists() and not capture.exists()
(root/'manifest.json').write_text(json.dumps({'previous_monitor':previous,'capture_base_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.suffix in ('.py','.html')},'dropins':[str(monitor),str(capture)]},indent=2))
try:
 monitor.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B '+str(root/'manage_server.py')+'\nUMask=0077\n')
 capture.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B '+str(root/'capture_control.py')+'\n')
 run('systemctl','--user','daemon-reload');run('systemctl','--user','restart','meteormesh-monitor.service')
 time.sleep(1)
 run('curl','-fsS','http://127.0.0.1:8765/healthz')
 assert 'rc218' in run('systemctl','--user','show','meteormesh-capture.service','-p','ExecStart','--value')
 print('DEPLOYED RC218; capture/analyzer not restarted; Tailscale unchanged')
except Exception:
 monitor.unlink(missing_ok=True);capture.unlink(missing_ok=True);run('systemctl','--user','daemon-reload');run('systemctl','--user','restart','meteormesh-monitor.service');raise
