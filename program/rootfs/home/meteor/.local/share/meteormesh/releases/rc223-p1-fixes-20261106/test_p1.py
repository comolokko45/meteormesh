import copy,datetime as dt,hashlib,importlib.util,io,json,sqlite3,subprocess,sys,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import autopilot_policy as policy
import initialize_empty_db as init
def load(name,file):
 spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
hook=load('hook','after_analysis_with_backup.py');gate=load('gate','post-analysis-condition-gate.py');outer=load('outer','cleanup_gate.py')
class P1(unittest.TestCase):
 def base(self):return {'night_status':{'night_id':'20261106-night','detector_summary':{'jobs_in_night':{'WAITING':0,'RUNNING':0,'SUCCEEDED':360,'FAILED':0},'analysis_complete':True}}}
 def test_failed_outside_window(self):
  d=self.base();d['night_status']['detector_summary']['jobs_in_night']['FAILED']=1
  r=policy.evaluate(d,{},dt.datetime.fromisoformat('2026-11-06T23:00:00+03:00'))[0]
  self.assertTrue(any(x['state']=='NEEDS_REVIEW' and x['target']=='analysis' for x in r['decisions']))
  d['night_status']['detector_summary']['jobs_in_night']['FAILED']=0
  d['units']={'meteormesh-analyze.service':{'result':'exit-code'}}
  r=policy.evaluate(d,{},dt.datetime.fromisoformat('2026-11-06T23:00:00+03:00'))[0]
  self.assertTrue(any(x['state']=='NEEDS_REVIEW' and x['target']=='analysis' for x in r['decisions']))
 def test_corrupt_telemetry(self):
  now=dt.datetime.now(dt.timezone.utc)
  variants=[{'assessment':None},{'phone_heartbeat':{'state':'READ_OK','devices':[]}}, {'phone_heartbeat':{'state':'READ_OK','devices':{'surya':None}}},{'phone_heartbeat':{'state':'READ_OK','devices':{'surya':{'storage':[]}}}},{'night_status':{'detector_summary':None,'phone_purge':None}},{'capture_progress':[]},{'units':{'meteormesh-hourly-pull.service':None}}]
  for d in variants:self.assertFalse(policy.evaluate(d,{'storage':None},now)[0]['mutations_enabled'])
 def test_timer_proof_required(self):
  d=self.base();old={'shadow_started_utc':'2026-11-06T22:00:00+03:00','shadow_night_id':'20261106-night'};now=dt.datetime.fromisoformat('2026-11-07T16:00:00+03:00')
  self.assertIn('PENDING',policy.evaluate(d,old,now)[0]['qualification'])
  d['predawn_timer_evidence']={'source':'SYSTEMD_PROPERTIES','night_id':'20261106-night','trigger_utc':'2026-11-07T05:30:23+03:00','service_started_utc':'2026-11-07T05:30:24+03:00','service_result':'success','invocation_id':'a'*32}
  d['predawn_timer_evidence']['collected_utc']=now.isoformat()
  self.assertIn('REVIEW_READY',policy.evaluate(d,old,now)[0]['qualification'])
  d['predawn_timer_evidence']['service_started_utc']='2026-11-07T06:10:00+03:00'
  self.assertIn('PENDING',policy.evaluate(d,old,now)[0]['qualification'])
 def test_cleanup_failure_exit(self):
  with patch.object(hook,'run_step',return_value=('ERROR','')):self.assertNotEqual(hook.main(),0)
  with patch.dict('os.environ',{'SERVICE_RESULT':'success'}),patch.object(gate.subprocess,'run',return_value=SimpleNamespace(stdout='',stderr='',returncode=7)):self.assertEqual(gate.main(),7)
  with patch.dict('os.environ',{'SERVICE_RESULT':'success'}),patch.object(outer.subprocess,'run',return_value=SimpleNamespace(returncode=7)):self.assertEqual(outer.main(),7)
  with patch.object(hook.subprocess,'run',return_value=SimpleNamespace(stdout='NEGATIVE_RETENTION: ERROR\n',stderr='',returncode=0)):
   self.assertTrue(hook.run_step('NEGATIVE_RETENTION',Path('dummy'),[],1)[0].startswith('ERROR'))
 def test_atomic_db(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(init.subprocess,'check_output',return_value='id'):
   root=Path(tmp)
   with self.assertRaises(sqlite3.Error):init.initialize(root,'CREATE TABLE a(x);INVALID SQL','id')
   self.assertFalse((root/'meteorhub.sqlite3').exists());self.assertFalse(list(root.glob('.meteorhub-init-*')))
   init.initialize(root,'CREATE TABLE a(x);','id')
   with self.assertRaises(FileExistsError):init.initialize(root,'','id')
 def test_restore_optimized_rejects_escape(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);script=root/'restore.py';script.write_bytes(Path(__file__).with_name('restore.py').read_bytes());raw=b'blocked'
   with tarfile.open(root/'program.tar.gz','w:gz') as t:
    info=tarfile.TarInfo('rootfs/etc/escape');info.size=len(raw);t.addfile(info,io.BytesIO(raw))
   (root/'manifest.json').write_text(json.dumps({'files':[{'path':'rootfs/etc/escape','sha256':hashlib.sha256(raw).hexdigest()}]}))
   (root/'SHA256SUMS').write_text('\n'.join(hashlib.sha256((root/n).read_bytes()).hexdigest()+'  '+n for n in ('program.tar.gz','manifest.json'))+'\n')
   r=subprocess.run([sys.executable,'-O',str(script),'--root',str(root/'target'),'--apply'],capture_output=True)
   self.assertNotEqual(r.returncode,0);self.assertFalse((root/'target/etc/escape').exists())
if __name__=='__main__':unittest.main()
