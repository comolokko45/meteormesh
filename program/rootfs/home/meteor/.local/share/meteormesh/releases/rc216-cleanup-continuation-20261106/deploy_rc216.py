from pathlib import Path
import subprocess,json,hashlib
root=Path.home()/'.local/share/meteormesh/releases/rc216-cleanup-continuation-20261106'
unit='meteormesh-analyze.service'
def run(*a):return subprocess.run(a,capture_output=True,text=True,check=True).stdout
old=run('systemctl','--user','show',unit,'-p','ExecStopPost','--value')
if 'rc210-backup-proof-20261105/ops/post-analysis-condition-gate.py' not in old:raise SystemExit('Unexpected active hook; refused')
p=Path.home()/'.config/systemd/user/meteormesh-analyze.service.d/~rc216-cleanup-continuation.conf'
if p.exists():raise SystemExit('Override exists')
text='[Service]\nExecStopPost=\nExecStopPost=/usr/bin/python3 -B '+str(root/'rc216_cleanup_gate.py')+'\n'
p.write_text(text)
run('systemctl','--user','daemon-reload')
active=run('systemctl','--user','show',unit,'-p','ExecStopPost','--value')
if str(root) not in active:
 p.unlink();run('systemctl','--user','daemon-reload');raise SystemExit('Override not effective; rolled back')
(root/'manifest.json').write_text(json.dumps({'previous_hook':old,'active_hook':active,'override':str(p),'sha256':hashlib.sha256((root/'rc216_cleanup_gate.py').read_bytes()).hexdigest(),'max_deletes':5,'analyzer_restarted':False},indent=2))
print(active)
