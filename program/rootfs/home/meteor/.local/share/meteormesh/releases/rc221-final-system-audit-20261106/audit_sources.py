import ast,hashlib,json
from pathlib import Path
root=Path.home()/'.local/share/meteormesh/releases'
names=['rc191-negative-retention-hardening-20261105','rc211-no-ready-segments-20261106','rc217-cleanup-capacity-20261106','rc218-mobile-management-20261106','rc220-agent-memory-20261106','control-013-20261101']
dirs=[root/n for n in names]+[Path.home()/'.local/lib/meteormesh',Path('/opt/meteormesh/MetDetPy')]
records=[];fail=[]
for directory in dirs:
 for p in directory.rglob('*.py'):
  if any(x in p.parts for x in ('.venv','venv','site-packages','__pycache__','.git')):continue
  try:
   raw=p.read_bytes();source=raw.decode('utf-8-sig');ast.parse(source,filename=str(p))
   records.append({'path':str(p),'lines':len(source.splitlines()),'sha256':hashlib.sha256(raw).hexdigest()})
  except Exception as e:fail.append({'path':str(p),'error':str(e)[:160]})
print(json.dumps({'files':len(records),'lines':sum(r['lines'] for r in records),'syntax_errors':fail,'manifest':records},indent=2))
