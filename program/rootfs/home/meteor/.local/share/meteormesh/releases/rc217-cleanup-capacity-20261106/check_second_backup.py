import importlib.util,json
from pathlib import Path
from unittest.mock import patch
p=Path(__file__).parent/'after_analysis_with_backup.py'
spec=importlib.util.spec_from_file_location('hook',p);h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
calls=[];backups=0
def step(label,*args):
 global backups
 calls.append(label)
 if label=='DB_BACKUP_PRE_RETENTION':
  backups+=1
  return ('OK' if backups==1 else 'ERROR_MISSING_BACKUP_RESULT','')
 return ('OK','NEGATIVE_RETENTION: '+json.dumps({'mode':'APPLY','deleted':5,'stopped_at_limit':True}))
with patch.object(h,'retention_enabled',return_value=True),patch.object(h,'run_step',side_effect=step):h.main()
assert calls==['DB_BACKUP_PRE_RETENTION','NEGATIVE_RETENTION','DB_BACKUP_PRE_RETENTION','REPORT']
print('PASS second backup failure prevents second deletion batch and still reports')
