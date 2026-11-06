import json,shutil,subprocess
from pathlib import Path
root=Path(__file__).resolve().parent
old=Path.home()/'.local/share/meteormesh/releases/rc215-final-audit-20261106/monitor'
new=root/'monitor'
shutil.copytree(old,new)
p=new/'index.html';s=p.read_text(encoding='utf-8')
before='her analiz kancasında en fazla 5 dosya.'
assert s.count(before)==1
p.write_text(s.replace(before,'parti başına en fazla 5, tur başına en fazla 2 parti (10 dosya). Her parti öncesinde yeni yedek doğrulanır.'),encoding='utf-8')
override=Path.home()/'.config/systemd/user/meteormesh-monitor.service.d/94-rc217-cleanup-label.conf'
assert not override.exists()
try:
 override.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B '+str(new/'meteor-monitor-server.py')+'\n',encoding='utf-8')
 subprocess.run(['systemctl','--user','daemon-reload'],check=True)
 subprocess.run(['systemctl','--user','restart','meteormesh-monitor.service'],check=True)
 print('MONITOR_LABEL_UPDATED; analyzer and capture untouched')
except Exception:
 override.unlink(missing_ok=True)
 subprocess.run(['systemctl','--user','daemon-reload'],check=True)
 subprocess.run(['systemctl','--user','restart','meteormesh-monitor.service'],check=True)
 raise
