import hashlib,http.client,json,tempfile,threading,uuid
from pathlib import Path
from unittest.mock import patch
import manage_server as m
import capture_control as c
from http.server import ThreadingHTTPServer
with tempfile.TemporaryDirectory() as tmp:
 m.ROOT=c.ROOT=Path(tmp);m.KEY=Path(tmp)/'key';m.KEY.write_text(hashlib.sha256(b'test-only-key').hexdigest());m.KEY.chmod(0o600);m.initialize()
 server=ThreadingHTTPServer(('127.0.0.1',0),m.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
 def call(path,doc=None,extra=None):
  conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
  headers={'Host':m.HOST,'Origin':m.ORIGIN,'Content-Type':'application/json'};headers.update(extra or {})
  conn.request('POST' if doc is not None else 'GET',path,body=json.dumps(doc) if doc is not None else None,headers=headers)
  r=conn.getresponse();result=(r.status,json.loads(r.read()),r.getheader('Set-Cookie'));conn.close();return result
 try:
  assert call('/api/manage/state')[0]==401
  assert call('/api/manage/login',{'key':'test-only-key'},{'Origin':'https://evil.test'})[0]==403
  assert call('/api/manage/login',{'key':'wrong'})[0]==401
  status,data,cookie=call('/api/manage/login',{'key':'test-only-key'});assert status==200 and 'HttpOnly' in cookie and 'Secure' in cookie
  headers={'Cookie':cookie.split(';')[0],'X-CSRF-Token':data['csrf']}
  request={'id':str(uuid.uuid4()),'action':'status','device':'','confirm':True}
  assert call('/api/manage/command',request,{'Cookie':headers['Cookie']})[0]==403
  assert call('/api/manage/command',{**request,'action':'shell'},headers)[0]==409
  assert call('/api/manage/command',{**request,'confirm':False},headers)[0]==409
  state={'ActiveState':'inactive','Result':'success','ExecMainStartTimestampMonotonic':'1'}
  with patch.object(m,'unit_state',return_value=state),patch.object(m.subprocess,'run') as run:
   assert call('/api/manage/command',request,headers)[0]==202
   assert call('/api/manage/command',request,headers)[0]==202
   assert run.call_count==1
   assert call('/api/manage/command',{**request,'id':str(uuid.uuid4())},headers)[0]==409
   assert m.update_receipts()[0]['status']=='ACCEPTED' # old success is not a new completion
  assert c.desired('cam-ruby',False,{'cam-ruby':{'mode':'start','until':200}},100)
  assert not c.desired('cam-ruby',True,{'cam-ruby':{'mode':'stop','until':200}},100)
  assert c.desired('cam-ruby',True,{'cam-ruby':{'mode':'stop','until':200}},201)
  assert not c.desired('cam-surya',False,{'cam-ruby':{'mode':'start','until':200}},100)
  with c.lock():
   try:
    with c.lock():raise AssertionError('duplicate lock entered')
   except BlockingIOError:pass
  c.save(c.ROOT/'requests.json',[{'status':'EXECUTING'}]);m.initialize();assert m.rows()[0]['status']=='INTERRUPTED_REVIEW_REQUIRED'
  assert call('/api/manage/logout',{},headers)[0]==200
  assert call('/api/manage/state',extra=headers)[0]==401
  print('PASS auth/origin/CSRF/allowlist/confirmation/idempotence/cooldown/old-result/expiry/isolation/lock/crash/logout')
 finally:server.shutdown();server.server_close()
