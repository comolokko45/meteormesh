"""Authenticated command surface in the existing loopback monitor process."""
import hashlib,hmac,json,os,re,secrets,subprocess,threading,time
from http.cookies import SimpleCookie
from pathlib import Path
import monitor_base as monitor
import capture_control as capture
HOST='meteorhub.tail520fb9.ts.net'
ORIGIN='https://'+HOST
ROOT=capture.ROOT
KEY=Path.home()/'.config/meteormesh/mobile-control-key.sha256'
MUTEX=threading.RLock()
SESSIONS={};ATTEMPTS=[]
UNITS={'status':'meteormesh-status.service','sync':'meteormesh-hourly-pull.service','analysis':'meteormesh-analyze.service','capture_check':'meteormesh-capture.service'}
LABELS={'status':'Durum bilgisini yenile','sync':'Doğrulanmış aktarım talebi','analysis':'Analiz turu talebi','capture_check':'Otomatik kayıt kontrolü','camera_start':'Seçili cihazda 1 saat kayıt','camera_stop':'Seçili cihazda 1 saat kayıt molası','camera_auto':'Seçili cihazı otomatiğe döndür'}
def rows():
 p=ROOT/'requests.json'
 if not p.exists():return []
 if p.is_symlink():raise RuntimeError('UNSAFE_AUDIT')
 value=json.loads(p.read_text())
 if not isinstance(value,list):raise RuntimeError('INVALID_AUDIT')
 return value
def write(rows_):capture.save(ROOT/'requests.json',rows_[-500:])
def unit_state(unit):
 r=subprocess.run(['systemctl','--user','show',unit,'-p','ActiveState','-p','SubState','-p','Result','-p','ExecMainStartTimestampMonotonic','-p','InvocationID'],capture_output=True,text=True,timeout=5,check=True)
 return dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)
def update_receipts():
 data=rows();changed=False
 for row in data:
  if row['status']!='ACCEPTED' or row['action'] not in UNITS:continue
  state=unit_state(UNITS[row['action']]);row['service']=state
  expected=row.get('invocation_id')
  if not expected:row['status']='DISPATCH_UNCERTAIN'
  elif state.get('InvocationID')!=expected:row['status']='SUPERSEDED_REVIEW_REQUIRED'
  elif state.get('ActiveState') in ('active','activating','deactivating'):pass
  elif state.get('Result')=='success':row['status']='SERVICE_COMPLETED'
  else:row['status']='BLOCKED_OR_FAILED'
  changed=True
 if changed:write(data)
 return data
def finish(request_id,result):
 with MUTEX:
  data=rows()
  for row in data:
   if row['id']==request_id:row.update(result)
  write(data)
def camera_job(request_id,device,mode):
 try:result=capture.apply(device,mode)
 except BlockingIOError:result={'status':'BUSY','detail':'Kayıt kontrolü sürüyor; sonraki kontrolden sonra yeniden deneyin.'}
 except Exception as exc:result={'status':'NEEDS_REVIEW','detail':type(exc).__name__+': '+str(exc)[:180]}
 finish(request_id,result)
def submit(doc):
 if set(doc)!={'id','action','device','confirm'} or doc['confirm'] is not True:raise ValueError('CONFIRMATION_REQUIRED')
 if not isinstance(doc['id'],str) or not re.fullmatch('[a-f0-9-]{36}',doc['id']):raise ValueError('INVALID_REQUEST_ID')
 action=doc['action'];device=doc['device']
 if action not in LABELS or (action.startswith('camera_') and device not in capture.DEVICES) or (not action.startswith('camera_') and device!=''):raise ValueError('ACTION_NOT_ALLOWED')
 with MUTEX:
  data=update_receipts()
  for row in data:
   if row['id']==doc['id']:
    if row['action']!=action or row['device']!=device:raise ValueError('REQUEST_ID_CONFLICT')
    return row
  now=time.time()
  if any(x['action']==action and x['device']==device and now-x['at']<60 for x in data):raise ValueError('COOLDOWN_60_SECONDS')
  if action.startswith('camera_') and any(x['status']=='EXECUTING' for x in data):raise ValueError('COMMAND_BUSY')
  before=unit_state(UNITS[action]) if action in UNITS else {}
  if action in UNITS and before['ActiveState'] not in ('inactive','failed'):raise ValueError('SERVICE_ALREADY_RUNNING')
  row={'id':doc['id'],'action':action,'device':device,'at':now,'monotonic':time.monotonic(),'status':'INTENT_RECORDED'}
  data.append(row);write(data) # persist before side effects; no replay after crash
  if action in UNITS:
   try:
    subprocess.run(['systemctl','--user','start','--no-block',UNITS[action]],capture_output=True,text=True,timeout=8,check=True)
    row['status']='DISPATCH_UNCERTAIN'
    for _ in range(10):
     state=unit_state(UNITS[action]);invocation=state.get('InvocationID')
     if invocation and invocation!=before.get('InvocationID'):
      row['invocation_id']=invocation;row['status']='ACCEPTED';break
     time.sleep(0.1)
   except Exception:row['status']='DISPATCH_UNCERTAIN'
   write(data)
  else:
   row['status']='EXECUTING';write(data)
   threading.Thread(target=camera_job,args=(row['id'],device,action.removeprefix('camera_')),daemon=True).start()
  return row
def initialize():
 ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
 if not KEY.is_file() or KEY.is_symlink() or KEY.stat().st_mode&0o077:raise RuntimeError('MISSING_OR_UNSAFE_MANAGEMENT_KEY')
 if not re.fullmatch('[a-f0-9]{64}',KEY.read_text().strip()):raise RuntimeError('INVALID_MANAGEMENT_KEY')
 data=rows()
 for row in data:
  if row['status'] in ('INTENT_RECORDED','EXECUTING','ACCEPTED'):row['status']='INTERRUPTED_REVIEW_REQUIRED'
 write(data)
class Handler(monitor.Handler):
 def reply(self,status,doc,cookie=None):
  body=json.dumps(doc,ensure_ascii=False).encode()
  if cookie:self._cookie=cookie
  self._headers(status,'application/json; charset=utf-8',len(body));self.wfile.write(body)
 def _headers(self,status,content_type,length):
  self.send_response(status)
  for k,v in {'Content-Type':content_type,'Content-Length':str(length),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}.items():self.send_header(k,v)
  if getattr(self,'_cookie',None):self.send_header('Set-Cookie',self._cookie);self._cookie=None
  self.end_headers()
 def session(self):
  try:
   cookie=SimpleCookie(self.headers.get('Cookie',''));sid=cookie['mm_session'].value
  except Exception:return None
  session=SESSIONS.get(sid)
  return session if session and session['expires']>time.time() else None
 def do_GET(self):
  if self.path=='/manage':
   body=Path(__file__).with_name('manage.html').read_bytes();self._headers(200,'text/html; charset=utf-8',len(body));self.wfile.write(body);return
  if self.path=='/api/manage/state':
   with MUTEX:
    if not self.session():self.reply(401,{'error':'LOGIN_REQUIRED'});return
    try:self.reply(200,{'actions':LABELS,'requests':update_receipts()[-30:],'capture_intents':capture.intents(),'unsupported':['Serbest terminal','G62/telefon reboot','APK yükleme','Keyfi dosya silme','Wi-Fi değiştirme'],'note':'SERVICE_COMPLETED yalnız servis bitişidir; dosya/analiz kanıtı monitörden doğrulanır.'})
    except Exception:self.reply(503,{'error':'STATE_UNAVAILABLE'})
   return
  super().do_GET()
 def do_POST(self):
  if self.client_address[0] not in ('127.0.0.1','::1') or self.headers.get('Host')!=HOST or self.headers.get('Origin')!=ORIGIN:self.reply(403,{'error':'ORIGIN_DENIED'});return
  if self.headers.get('Content-Type')!='application/json' or self.headers.get('Transfer-Encoding'):self.reply(415,{'error':'JSON_REQUIRED'});return
  try:
   size=int(self.headers.get('Content-Length','0'))
   if not 0<size<=2048:raise ValueError()
   self.connection.settimeout(5);doc=json.loads(self.rfile.read(size))
   if not isinstance(doc,dict):raise ValueError()
  except Exception:self.reply(400,{'error':'INVALID_BODY'});return
  with MUTEX:
   if self.path=='/api/manage/login':
    now=time.time();ATTEMPTS[:]=[t for t in ATTEMPTS if now-t<60]
    if len(ATTEMPTS)>=5:self.reply(429,{'error':'WAIT_60_SECONDS'});return
    ATTEMPTS.append(now)
    key=doc.get('key')
    if not isinstance(key,str) or not hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(),KEY.read_text().strip()):self.reply(401,{'error':'INVALID_KEY'});return
    for sid in list(SESSIONS):
     if SESSIONS[sid]['expires']<=now:del SESSIONS[sid]
    if len(SESSIONS)>=32:self.reply(429,{'error':'SESSION_LIMIT'});return
    sid=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32);SESSIONS[sid]={'expires':now+1200,'csrf':csrf}
    self.reply(200,{'csrf':csrf,'expires':now+1200},'mm_session='+sid+'; HttpOnly; Secure; SameSite=Strict; Path=/api/manage; Max-Age=1200');return
   session=self.session()
   if not session or not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf']):self.reply(403,{'error':'SESSION_OR_CSRF_INVALID'});return
   if self.path=='/api/manage/logout':
    session['expires']=0;self.reply(200,{'ok':True},'mm_session=; HttpOnly; Secure; SameSite=Strict; Path=/api/manage; Max-Age=0');return
   if self.path!='/api/manage/command':self.reply(404,{'error':'NOT_FOUND'});return
   try:self.reply(202,submit(doc))
   except ValueError as exc:self.reply(409,{'error':str(exc)})
   except Exception:self.reply(503,{'error':'COMMAND_NOT_CONFIRMED'})
if __name__=='__main__':
 initialize();monitor.Handler=Handler;monitor.main()
