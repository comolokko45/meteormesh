from pathlib import Path
import hashlib,json,subprocess,time,urllib.request
from datetime import datetime,timezone
root=Path(__file__).resolve().parent
previous=Path('/home/meteor/.local/share/meteormesh/releases/rc213-monitor-live-summary-20261106/monitor/meteor-monitor-server.py')
expected='f3ba24f4a6dbfe68c45143d03b6161f87f00cb84cc547dbcb862b50b7b4e219e'
if hashlib.sha256(previous.read_bytes()).hexdigest()!=expected: raise SystemExit('Previous source changed; deployment refused')
drop=Path.home()/'.config/systemd/user/meteormesh-monitor.service.d/92-rc214-integrity.conf'
if drop.exists(): raise SystemExit('Existing override; refused')
manifest={'created_utc':datetime.now(timezone.utc).isoformat(),'release':str(root),'previous':str(previous.parent.parent),'sha256':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'monitor').iterdir() if p.is_file()},'tests':'11 isolated gate cases + 9 JavaScript refresh cases passed locally','scope':'Monitor only; no video/database/capture/analyzer mutation'}
(root/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
drop.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B '+str(root/'monitor/meteor-monitor-server.py')+'\n')
try:
 subprocess.run(['systemctl','--user','daemon-reload'],check=True)
 subprocess.run(['systemctl','--user','restart','meteormesh-monitor.service'],check=True)
 for attempt in range(15):
  try:
   assert urllib.request.urlopen('http://127.0.0.1:8765/healthz',timeout=2).read().strip()==b'ok'
   break
  except Exception:
   if attempt==14: raise
   time.sleep(.3)
 state=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/state',timeout=35))
 if state.get('schema')!='meteormesh.monitor.v1' or state.get('source_error'): raise RuntimeError('Live state source invalid')
 evidence={k:state.get(k) for k in ('generated_local','overall','source','ops_alerts')}
 evidence.update(gates=state['automation_runs']['natural_timer_gates'],queue=state['night']['capture_analysis'],timers=[t for t in state['timers'] if 'morning' in t['unit'] or 'predawn' in t['unit']])
 (root/'live-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(evidence,ensure_ascii=False))
except Exception:
 drop.unlink()
 subprocess.run(['systemctl','--user','daemon-reload'],check=True)
 subprocess.run(['systemctl','--user','restart','meteormesh-monitor.service'],check=True)
 raise
