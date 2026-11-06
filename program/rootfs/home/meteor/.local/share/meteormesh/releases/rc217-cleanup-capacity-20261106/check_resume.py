"""Actual RC191 downloader with a small local HTTP interruption fixture."""
import hashlib,http.client,http.server,json,socket,sys,tempfile,threading
from pathlib import Path
sys.path.insert(0,str(Path.home()/'.local/share/meteormesh/releases/rc191-negative-retention-hardening-20261105/MeteorHub'))
from meteornode_pull import MeteorNode
payload=b'RC217-fixture-only-'*140000
requests=[]
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  value=self.headers.get('Range');requests.append(value)
  offset=int(value.split('=')[1].split('-')[0]) if value else 0
  self.send_response(206 if value else 200)
  self.send_header('Content-Length',str(len(payload)-offset))
  if value:self.send_header('Content-Range',f'bytes {offset}-{len(payload)-1}/{len(payload)}')
  self.end_headers()
  self.wfile.write(payload[offset:] if value else payload[:1100000]);self.wfile.flush()
  if not value:self.connection.shutdown(socket.SHUT_RDWR);self.connection.close()
class Client(MeteorNode):
 def connection(self):return http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
try:
 with tempfile.TemporaryDirectory(prefix='rc217-resume-') as tmp:
  dest=Path(tmp);client=Client('127.0.0.1',server.server_port,'0'*64,'fixture-not-real-secret-token-0000')
  segment={'segment_id':'rc217_fixture','size_bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
  try:client.pull_one(segment,dest)
  except (RuntimeError,http.client.IncompleteRead):pass
  else:raise AssertionError('First interrupted transfer unexpectedly complete')
  part=dest/'rc217_fixture.mp4.part';size=part.stat().st_size
  assert 0<size<len(payload) and not (dest/'rc217_fixture.mp4').exists()
  result=client.pull_one(segment,dest)
  assert requests==[None,f'bytes={size}-']
  assert (dest/'rc217_fixture.mp4').read_bytes()==payload and not part.exists()
  assert client.pull_one(segment,dest)=='already verified + sidecar reconciled'
  assert len(requests)==2
  print(json.dumps({'status':'PASS','scope':'isolated HTTP interruption, actual RC191 pull_one; not a phone or reboot acceptance','partial_bytes':size,'total_bytes':len(payload),'range':requests[1],'final_sha256':segment['sha256'],'repeat_download_requests':0,'result':result}))
finally:server.shutdown();server.server_close()
