import importlib.util,json
from pathlib import Path
from unittest.mock import patch
p=Path(__file__).parent/'after_analysis_with_backup.py';sp=importlib.util.spec_from_file_location('hook',p);h=importlib.util.module_from_spec(sp);sp.loader.exec_module(h)
full='NEGATIVE_RETENTION: '+json.dumps(dict(mode='APPLY',deleted=5,stopped_at_limit=True))
for text in ('', 'NEGATIVE_RETENTION: SKIPPED_ANALYSIS_OR_SYNC_LOCKED','NEGATIVE_RETENTION: ERROR',full.replace('5','4'),full.replace('true','false')):assert not h.full_successful_batch(text)
assert h.full_successful_batch(full)
for enabled,backup_ok,ret_status,output,expected in [(True,True,'OK',full,2),(True,False,'OK',full,0),(True,True,'ERROR_TIMEOUT',full,1),(True,True,'OK','',1),(False,True,'OK',full,1)]:
 calls=[]
 def run(label,*args):
  calls.append((label,args))
  return ('OK' if backup_ok else 'ERROR','') if label=='DB_BACKUP_PRE_RETENTION' else (ret_status,output) if label=='NEGATIVE_RETENTION' else ('OK','')
 with patch.object(h,'retention_enabled',return_value=enabled),patch.object(h,'run_step',side_effect=run):h.main()
 retention=[x for x in calls if x[0]=='NEGATIVE_RETENTION'];assert len(retention)==expected
 for _,args in retention:assert args[1][-1]=='5'
 assert calls[-1][0]=='REPORT'
print('PASS 11 parsing/bounded batching/backup/failure cases')
