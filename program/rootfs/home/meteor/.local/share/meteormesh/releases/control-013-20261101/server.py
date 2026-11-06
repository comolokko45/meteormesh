#!/usr/bin/env python3
"""MeteorMesh 0.13 LAN control: certificate-pinned device heartbeats and safe local commands.

Strictly in-memory state. Never accesses the MeteorHub data HDD, camera, SQLite, videos,
or phone storage. Phones initiate outbound HTTPS; no shell or delete actions.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hmac
import http.server
import json
import re
import secrets
import socket
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

DEVICES=('cam-viva','cam-ruby','cam-surya')
HEX32=re.compile(r'^[0-9a-f]{32}$')
HEX64=re.compile(r'^[0-9a-f]{64}$')
VALID_STATES={'idle','armed_idle','recording','stopped','error','unknown'}
MAX_BODY=4096

class ControlState:
    def __init__(self, tokens:dict[str,str], clock=time.time):
        if set(tokens)!=set(DEVICES) or any(not HEX64.fullmatch(t) for t in tokens.values()):
            raise ValueError('Invalid device token configuration')
        if len(set(tokens.values()))!=len(tokens):raise ValueError('Duplicate device tokens')
        self.tokens=tokens
        self.clock=clock
        self.lock=threading.Lock()
        self.last={}
        self.seen={d:{} for d in DEVICES}
        self.pending={}

    def authenticate(self, device, authorization):
        expected=self.tokens.get(device)
        if expected is None or not isinstance(authorization,str) or not authorization.startswith('Bearer '):
            return False
        candidate=authorization[7:]
        return HEX64.fullmatch(candidate) is not None and hmac.compare_digest(candidate,expected)

    def heartbeat(self, device, payload, authorization):
        if not self.authenticate(device,authorization):return 401,{'error':'unauthorized'}
        if not isinstance(payload,dict) or set(payload)!={'device_id','timestamp_ms','nonce','status','acked_command_id'}:
            return 400,{'error':'bad_schema'}
        if payload['device_id']!=device or type(payload['timestamp_ms']) is not int or not HEX32.fullmatch(str(payload['nonce'])):
            return 400,{'error':'invalid_identity_or_timestamp'}
        now=self.clock()
        if abs(now*1000-payload['timestamp_ms'])>300000:return 400,{'error':'clock_skew'}
        status=payload['status']
        allowed={'camera_state','lan_listening','ready_segments','free_mib','app_version','lan_ip'}
        if not isinstance(status,dict) or set(status)!=allowed:
            return 400,{'error':'bad_status'}
        if (not isinstance(status['camera_state'],str) or status['camera_state'] not in VALID_STATES
            or type(status['lan_listening']) is not bool
            or type(status['ready_segments']) is not int or not 0<=status['ready_segments']<=100000
            or type(status['free_mib']) is not int or not 0<=status['free_mib']<=1000000
            or not isinstance(status['app_version'],str) or len(status['app_version'])>80
            or not isinstance(status['lan_ip'],str) or len(status['lan_ip'])>45):
            return 400,{'error':'bad_status_values'}
        ack=payload['acked_command_id']
        if ack is not None and (not isinstance(ack,str) or not HEX32.fullmatch(ack)):
            return 400,{'error':'bad_ack'}
        with self.lock:
            recent=self.seen[device]
            for nonce,seen_at in list(recent.items()):
                if now-seen_at>600:del recent[nonce]
            if payload['nonce'] in recent:return 409,{'error':'replayed_nonce'}
            if len(recent)>=256:return 429,{'error':'too_many_nonces'}
            recent[payload['nonce']]=now
            if ack is not None and self.pending.get(device,{}).get('id')==ack:
                del self.pending[device]
            self.last[device]={'last_seen_utc':dt.datetime.fromtimestamp(now,dt.timezone.utc).isoformat(),
                               'seen_at':now, **status}
            next_command=self.pending.get(device)
            return 200,{'ok':True,'interval_seconds':60,'command':next_command}

    def queue_command(self, device, command):
        if device not in DEVICES:return 404,{'error':'unknown_device'}
        if command!='restart_listener':return 400,{'error':'command_not_allowed'}
        with self.lock:
            if device in self.pending:return 409,{'error':'command_pending'}
            obj={'id':secrets.token_hex(16),'action':command}
            self.pending[device]=obj
            return 202,{'queued':True,'device_id':device,'command_id':obj['id']}

    def status(self):
        now=self.clock()
        with self.lock:
            return {'schema':'meteormesh.control-status.v1','devices':{
                d:({**{k:v for k,v in self.last[d].items() if k!='seen_at'},
                   'age_seconds':max(0,int(now-self.last[d]['seen_at'])),
                   'online':(now-self.last[d]['seen_at']<180),
                   'command_pending':d in self.pending} if d in self.last
                  else {'online':False,'command_pending':d in self.pending})
                for d in DEVICES}}


def make_handler(state:ControlState):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version='MeteorMeshControl/0.13';sys_version=''
        def log_message(self, fmt,*args):
            # Prevent accidental token/body leakage into journald.
            pass
        def reply(self,code:int,obj:dict):
            raw=json.dumps(obj,separators=(',',':'),sort_keys=True).encode()
            self.send_response(code)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def read_json(self):
            try: size=int(self.headers.get('Content-Length','-1'))
            except ValueError:raise ValueError('invalid_content_length')
            if not 0<size<=MAX_BODY:raise ValueError('invalid_content_length')
            if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':
                raise ValueError('content_type')
            obj=json.loads(self.rfile.read(size).decode('utf-8'))
            if not isinstance(obj,dict):raise ValueError('json_object_required')
            return obj
        def do_GET(self):
            if urlsplit(self.path).path!='/v1/status' or self.path!='/v1/status':
                return self.reply(404,{'error':'not_found'})
            if self.client_address[0] not in ('127.0.0.1','::1'):
                return self.reply(403,{'error':'local_only'})
            return self.reply(200,state.status())
        def do_POST(self):
            path=urlsplit(self.path).path
            if path not in ('/v1/heartbeat','/v1/command') or path!=self.path:
                return self.reply(404,{'error':'not_found'})
            if path=='/v1/command' and self.client_address[0] not in ('127.0.0.1','::1'):
                return self.reply(403,{'error':'local_only'})
            if path=='/v1/heartbeat':
                # Reject unknown/authless request before parsing body.
                device=self.headers.get('X-Meteor-Device','')
                if not state.authenticate(device,self.headers.get('Authorization','')):
                    return self.reply(401,{'error':'unauthorized'})
            try:obj=self.read_json()
            except (ValueError,UnicodeError,json.JSONDecodeError):
                return self.reply(400,{'error':'invalid_json'})
            if path=='/v1/heartbeat':
                result=state.heartbeat(device,obj,self.headers.get('Authorization',''))
            else:
                if (set(obj)!={'device_id','action'} or not isinstance(obj['device_id'],str)
                    or not isinstance(obj['action'],str)):
                    return self.reply(400,{'error':'bad_schema'})
                result=state.queue_command(obj['device_id'],obj['action'])
            self.reply(*result)
    return Handler

class HTTP(http.server.ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=8
    allow_reuse_address=True
    def get_request(self):
        sock,addr=super().get_request()
        sock.settimeout(8)
        return sock,addr


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--config',type=Path,required=True)
    args=ap.parse_args()
    path=args.config
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise SystemExit('CONTROL_CONFIG_UNSAFE')
    cfg=json.loads(path.read_text())
    if set(cfg)!={'cert','key','tokens','port','bind'} or cfg['port']!=8443 or cfg['bind']!='0.0.0.0':
        raise SystemExit('CONTROL_CONFIG_UNEXPECTED')
    for field in ('cert','key'):
        p=Path(cfg[field]);
        if p.is_symlink() or not p.is_file() or p.stat().st_mode & 0o077:
            raise SystemExit('TLS_FILE_UNSAFE:'+field)
    state=ControlState(cfg['tokens'])
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version=ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cfg['cert'],cfg['key'])
    h=HTTP((cfg['bind'],cfg['port']),make_handler(state))
    h.socket=ctx.wrap_socket(h.socket,server_side=True)
    print('METEOR_CONTROL_013: LISTENING_8443',flush=True)
    h.serve_forever(poll_interval=.5)

if __name__=='__main__': main()
