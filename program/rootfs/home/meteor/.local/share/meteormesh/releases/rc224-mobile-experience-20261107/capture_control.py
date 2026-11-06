"""Shared lock and expiring manual intent around the unchanged capture engine."""
import contextlib,fcntl,json,os,tempfile,time
from pathlib import Path
import capture_base as base
ROOT=Path.home()/'.local/state/meteormesh/mobile-control'
DEVICES={'cam-ruby','cam-surya','cam-viva'}
def save(path,doc):
 path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 if path.is_symlink():raise RuntimeError('UNSAFE_STATE_PATH')
 fd,tmp=tempfile.mkstemp(dir=path.parent)
 try:
  with os.fdopen(fd,'w',encoding='utf-8') as f:
   os.fchmod(f.fileno(),0o600);json.dump(doc,f);f.flush();os.fsync(f.fileno())
  os.replace(tmp,path)
  directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
  try:os.fsync(directory)
  finally:os.close(directory)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
@contextlib.contextmanager
def lock():
 ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
 with (ROOT/'capture.lock').open('a') as f:
  fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  try:yield
  finally:fcntl.flock(f,fcntl.LOCK_UN)
def intents():
 p=ROOT/'capture-intent.json'
 if not p.exists():return {}
 if p.is_symlink():raise RuntimeError('UNSAFE_STATE_PATH')
 doc=json.loads(p.read_text())
 if not isinstance(doc,dict):raise RuntimeError('INVALID_INTENT')
 for k,v in doc.items():
  if k not in DEVICES or v.get('mode') not in ('start','stop') or not isinstance(v.get('until'),(int,float)):raise RuntimeError('INVALID_INTENT')
 return doc
def desired(device,automatic,doc,now=None):
 now=time.time() if now is None else now
 row=doc.get(device,{})
 return row['mode']=='start' if row.get('until',0)>now else automatic
def tick():
 with lock():
  intent=intents();original=base.process_one
  def controlled(d,doc,night):return original(d,doc,desired(d['id'],night,intent))
  base.process_one=controlled
  base.run_once(False)
def apply(device,mode,duration=60):
 if device not in DEVICES or mode not in ('start','stop','auto'):raise ValueError('INVALID_ACTION')
 if type(duration) is not int or duration not in (15,30,60):raise ValueError('DURATION_NOT_ALLOWED')
 with lock():
  d=next(x for x in base.read_devices() if x['id']==device)
  doc=base.read_state();intent=intents()
  if mode=='auto':
   intent.pop(device,None);save(ROOT/'capture-intent.json',intent)
  h=base.health(d)
  if mode=='auto':pass
  else:intent[device]={'mode':mode,'until':time.time()+duration*60}
  # Record intent before command. The selected duration survives a process crash.
  save(ROOT/'capture-intent.json',intent)
  if mode=='stop' and str(h.get('state','')).startswith('recording') and device not in doc['owned']:
   doc['owned'][device]={'since_utc':base.dt.datetime.now(base.dt.timezone.utc).isoformat(),'remote_stop_requested':True};base.save_state(doc)
  result=base.process_one(d,doc,desired(device,base.inside_window(),intent))
  current=base.health(d)
  want=desired(device,base.inside_window(),intent)
  recorded=str(current.get('state','')).startswith('recording')
  # Auto daytime may deliberately preserve an unowned manually started session.
  verified=(recorded if want else current.get('state') in ('idle','armed_idle')) or (mode=='auto' and result=='DAY_MANUAL_UNTOUCHED')
  return {'status':'VERIFIED' if verified else 'NEEDS_REARM_OR_STORAGE','detail':result,'camera_state':current.get('state'),'intent':intent.get(device,{'mode':'auto'})}
if __name__=='__main__':tick()
