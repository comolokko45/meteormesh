"""Real HTTPS control acceptance; only refreshes existing status service."""
import http.cookiejar,json,time,uuid
from pathlib import Path
from urllib.request import build_opener,HTTPCookieProcessor,Request
from urllib.error import HTTPError
origin='https://meteorhub.tail520fb9.ts.net'
opener=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
def call(path,doc=None,csrf=None):
 headers={'Origin':origin}
 if doc is not None:headers['Content-Type']='application/json'
 if csrf:headers['X-CSRF-Token']=csrf
 with opener.open(Request(origin+path,data=json.dumps(doc).encode() if doc is not None else None,headers=headers),timeout=15) as r:return json.load(r)
try:call('/api/manage/state')
except HTTPError as exc:assert exc.code==401
else:raise AssertionError('Unauthenticated management exposed')
key=(Path.home()/'.local/share/meteormesh/releases/rc218-mobile-management-20261106/YONETIM_ANAHTARI.txt').read_text().strip()
auth=call('/api/manage/login',{'key':key});csrf=auth['csrf']
rid=str(uuid.uuid4());doc={'id':rid,'action':'status','device':'','confirm':True}
reply=call('/api/manage/command',doc,csrf)
assert reply['status']=='ACCEPTED'
assert call('/api/manage/command',doc,csrf)['id']==rid
result=None
for _ in range(6):
 time.sleep(2)
 state=call('/api/manage/state');result=next(r for r in state['requests'] if r['id']==rid)
 if result['status']!='ACCEPTED':break
call('/api/manage/logout',{},csrf)
print(json.dumps({'https':'TLS_VERIFIED','unauthenticated':'DENIED','login':'PASS','idempotent_request':rid,'status_refresh_result':result,'camera_commands_executed':False,'reboot_or_delete_executed':False},indent=2))
assert result['status']=='SERVICE_COMPLETED',result['status']
