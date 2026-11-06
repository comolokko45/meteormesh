from pathlib import Path
import hashlib,json,subprocess,time,urllib.request
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
root=Path(__file__).resolve().parent
prev=Path.home()/'.local/share/meteormesh/releases/rc214-monitor-integrity-20261106'
meta=json.loads((prev/'manifest.json').read_text())
if hashlib.sha256((prev/'monitor/meteor-monitor-server.py').read_bytes()).hexdigest()!=meta['sha256']['monitor/meteor-monitor-server.py']:raise SystemExit('Previous release drift')
def run(*args):return subprocess.run(args,capture_output=True,text=True,check=True).stdout.strip()
if str(prev) not in run('systemctl','--user','show','meteormesh-monitor.service','-p','ExecStart','--value'):raise SystemExit('Unexpected active monitor')
now=datetime.now(ZoneInfo('Europe/Istanbul'))
if not 5<=now.hour<19:raise SystemExit('Outside daytime maintenance')
state=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/state',timeout=35))
if any(p.get('camera') not in ('idle','armed_idle','stopped') for p in state['phones']):raise SystemExit('Camera state unsafe for capture config change')
if run('systemctl','--user','show','meteormesh-capture.service','-p','ActiveState','--value')!='inactive':raise SystemExit('Capture currently running; defer')
route=run('/usr/bin/python3',str(root/'meteor-capture-lan-ready.py'),'--timeout','0')
monitor=Path.home()/'.config/systemd/user/meteormesh-monitor.service.d/93-rc215-coverage-policy.conf'
capture=Path.home()/'.config/systemd/user/meteormesh-capture.service.d/zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz-rc215-capture-lan.conf'
if monitor.exists() or capture.exists():raise SystemExit('Existing override')
monitor.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B '+str(root/'monitor/meteor-monitor-server.py')+'\n')
capture.write_text('[Service]\nExecStartPre=\nExecStartPre=/usr/bin/python3 -B '+str(root/'meteor-capture-lan-ready.py')+' --timeout 60\n')
try:
 run('systemctl','--user','daemon-reload')
 run('systemctl','--user','restart','meteormesh-monitor.service')
 for i in range(15):
  try:
   assert urllib.request.urlopen('http://127.0.0.1:8765/healthz',timeout=2).read().strip()==b'ok';break
  except Exception:
   if i==14:raise
   time.sleep(.3)
 state=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/state',timeout=35))
 if state.get('source_error'):raise RuntimeError('Monitor source failure')
 evidence={'at':state['generated_local'],'route':route,'capture_pre':run('systemctl','--user','show','meteormesh-capture.service','-p','ExecStartPre','--value'),'queue':state['night']['capture_analysis'],'cleanup':state['night']['hdd_cleanup'],'gaps':state['segments']['gaps'],'overall':state['overall']}
 (root/'live-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
 (root/'manifest.json').write_text(json.dumps({'created_utc':datetime.now(timezone.utc).isoformat(),'previous':str(prev),'dropins':[str(monitor),str(capture)],'sha256':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [root/'monitor/meteor-monitor-server.py',root/'monitor/index.html',root/'meteor-capture-lan-ready.py']}},indent=2))
 print(json.dumps(evidence,ensure_ascii=False))
except Exception:
 monitor.unlink();capture.unlink();run('systemctl','--user','daemon-reload');run('systemctl','--user','restart','meteormesh-monitor.service');raise
