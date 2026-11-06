import importlib.util,json, pathlib,secrets,time,unittest
PATH=pathlib.Path(__file__).resolve().parent/'server.py'
spec=importlib.util.spec_from_file_location('meteor_control',PATH);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
TOKENS={d:secrets.token_hex(32) for d in m.DEVICES}

def hb(device='cam-viva',nonce=None,ack=None):
    return {'device_id':device,'timestamp_ms':round(time.time()*1000),'nonce':nonce or secrets.token_hex(16),
            'status':{'camera_state':'recording','lan_listening':True,'ready_segments':4,'free_mib':12000,
                      'app_version':'0.13.1','lan_ip':'192.168.0.216'},'acked_command_id':ack}

class TestControl(unittest.TestCase):
 def setUp(self):self.state=m.ControlState(TOKENS)
 def auth(self,d):return 'Bearer '+TOKENS[d]
 def test_connected_devices(self):
    for d in m.DEVICES:
        code,response=self.state.heartbeat(d,hb(d),self.auth(d));self.assertEqual(code,200)
    self.assertEqual(sum(x['online'] for x in self.state.status()['devices'].values()),3)
 def test_invalid_identity_and_token(self):
    p=hb('cam-viva')
    self.assertEqual(self.state.heartbeat('cam-viva',p,self.auth('cam-ruby'))[0],401)
    self.assertEqual(self.state.heartbeat('cam-ruby',p,self.auth('cam-ruby'))[0],400)
 def test_nonce_replay_and_clock(self):
    payload=hb();self.assertEqual(self.state.heartbeat('cam-viva',payload,self.auth('cam-viva'))[0],200)
    self.assertEqual(self.state.heartbeat('cam-viva',payload,self.auth('cam-viva'))[0],409)
    payload=hb();payload['timestamp_ms']-=600000
    self.assertEqual(self.state.heartbeat('cam-viva',payload,self.auth('cam-viva'))[0],400)
 def test_bounded_commands_ack(self):
    code,cmd=self.state.queue_command('cam-surya','restart_listener');self.assertEqual(code,202)
    self.assertEqual(self.state.queue_command('cam-surya','restart_listener')[0],409)
    code,reply=self.state.heartbeat('cam-surya',hb('cam-surya'),self.auth('cam-surya'))
    self.assertEqual(reply['command']['id'],cmd['command_id'])
    self.state.heartbeat('cam-surya',hb('cam-surya',ack=cmd['command_id']),self.auth('cam-surya'))
    self.assertFalse(self.state.status()['devices']['cam-surya']['command_pending'])
 def test_disallow_any_other_commands(self):
    for cmd in ('delete','start_camera','stop_recording','shell','upload'):
        self.assertEqual(self.state.queue_command('cam-viva',cmd)[0],400)
 def test_bad_payloads(self):
    for v in ('bogus',[],{},False):
        p=hb();p['status']['camera_state']=v
        self.assertEqual(self.state.heartbeat('cam-viva',p,self.auth('cam-viva'))[0],400)
    p=hb();p['status']['ready_segments']=-1
    self.assertEqual(self.state.heartbeat('cam-viva',p,self.auth('cam-viva'))[0],400)
 def test_offline(self):
    clock=[10000.]
    state=m.ControlState(TOKENS,clock=lambda:clock[0]);p=hb();p['timestamp_ms']=int(clock[0]*1000)
    self.assertEqual(state.heartbeat('cam-viva',p,self.auth('cam-viva'))[0],200)
    clock[0]+=181
    self.assertFalse(state.status()['devices']['cam-viva']['online'])
 def test_no_hdd_or_db_references(self):
    code=PATH.read_text();self.assertNotIn('/srv/meteor-data',code)
    self.assertNotIn('sqlite3',code)

if __name__=='__main__':unittest.main()
