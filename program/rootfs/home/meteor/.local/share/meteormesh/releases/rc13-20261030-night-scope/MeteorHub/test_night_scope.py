import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from night_scope import load_night_scope, NightScope
import hub_workflow
import sqlite3

class NightScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'night.json'
        self.stamp = datetime(2026, 10, 31, 17, tzinfo=timezone.utc)
        self.base = {'schema':'meteormesh.night_scope.v1', 'status':'ARMED',
                     'night_id':'20261031-night', 'baseline_created_utc':'2026-10-30T17:18:57Z',
                     'start_utc_ms':int(self.stamp.timestamp()*1000),
                     'end_utc_ms':int((self.stamp+timedelta(hours=10)).timestamp()*1000),
                     'ingest_scope_enforced':True, 'queue_scope_enforced':True,
                     'automatic_run_allowed':True,
                     'excluded_segments':{'cam-viva':{'viva_20261029T145445Z_33edfe7dfbeb':'a'*64},
                                          'cam-ruby':{'ruby_20261030T150000Z_123456789abc':'b'*64},
                                          'cam-surya':{'surya_20261030T150000Z_123456789abc':'c'*64}}}
        self.save()
    def save(self):
        self.path.write_text(json.dumps(self.base))
        self.path.chmod(0o600)
    def test_denied_disarmed(self):
        self.base['status']='DISARMED';self.save()
        with self.assertRaisesRegex(RuntimeError, 'not ARMED'):load_night_scope(self.path)
    def test_denied_missing_scope(self):
        with self.assertRaises(RuntimeError):load_night_scope(self.path.parent/'absent')
    def test_denied_unsecured_scope(self):
        self.path.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, 'group/others'):load_night_scope(self.path)
    def test_denied_bad_time(self):
        self.base['start_utc_ms']=int(datetime(2026,10,30,17,tzinfo=timezone.utc).timestamp()*1000);self.base['end_utc_ms']=int(datetime(2026,10,30,20,tzinfo=timezone.utc).timestamp()*1000);self.save()
        with self.assertRaisesRegex(RuntimeError, 'before saved inventory'):load_night_scope(self.path)
    def test_excluded_and_new_and_bad(self):
        s=load_night_scope(self.path)
        self.assertFalse(s.allows('cam-viva','viva_20261029T145445Z_33edfe7dfbeb','a'*64))
        self.assertTrue(s.allows('cam-viva','viva_20261031T175000Z_123456789abc','d'*64))
        self.assertFalse(s.allows('cam-viva','viva_20261101T045000Z_123456789abc','d'*64))
        self.assertFalse(s.allows('cam-viva','invalid','d'*64))
        with self.assertRaisesRegex(RuntimeError,'digest changed'):
            s.allows('cam-viva','viva_20261029T145445Z_33edfe7dfbeb','f'*64)
    def test_queue_only_current_night(self):
        s=load_night_scope(self.path)
        db=sqlite3.connect(':memory:')
        hub_workflow.init_pipeline(db)
        db.execute("CREATE TABLE segments (device_id TEXT, segment_id TEXT,sha256 TEXT,state TEXT)")
        for seg in ('viva_20261029T145445Z_33edfe7dfbeb','viva_20261031T175000Z_123456789abc'):
            db.execute("INSERT INTO segments VALUES (?,?,?,?)",('cam-viva',seg,'a'*64 if '20261029' in seg else 'd'*64,'INGEST_VERIFIED'))
        count=hub_workflow.enqueue_verified(db,'e'*64,scope=s)
        self.assertEqual(count,1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)
        job=hub_workflow.claim_next(db,scope=s)
        self.assertIn('20261031',job['segment_id'])
        db.close()
    def test_existing_old_waiting_job_skipped(self):
        s=load_night_scope(self.path)
        db=sqlite3.connect(':memory:');hub_workflow.init_pipeline(db)
        db.execute("INSERT INTO jobs(unique_key,kind,device_id,segment_id,video_sha256,detector_version,detector_config_sha256,state,created_utc) VALUES (?,?,?,?,?,?,?,?,?)",
                   ('x'*64,'DETECT','cam-viva','viva_20261029T145445Z_33edfe7dfbeb','a'*64,'v2','e'*64,'WAITING','2026'))
        db.commit()
        self.assertIsNone(hub_workflow.claim_next(db,scope=s))
        self.assertEqual(db.execute('SELECT state FROM jobs').fetchone()[0],'WAITING')
        db.close()
    def test_cli_requires_scope_before_storage(self):
        import meteor_morning
        with patch('meteor_morning._mm01_verify') as verify:
            with self.assertRaisesRegex(RuntimeError,'NIGHT_SCOPE_REQUIRED_FOR_RC13'):
                meteor_morning.main(['run','--config',str(self.path),'--data-dir',str(self.path.parent),
                    '--metdet-repo',str(self.path.parent),'--metdet-config',str(self.path)])
            verify.assert_not_called()
    def test_disarmed_denies_before_storage_writes(self):
        import meteor_morning
        self.base['status']='DISARMED';self.save()
        with patch('meteor_morning._mm01_verify') as verify:
            with self.assertRaisesRegex(RuntimeError,'not ARMED'):
                meteor_morning.run_morning(config=self.path, data_root=self.path.parent,
                    detector_repo=self.path.parent, detector_config=self.path,
                    night_scope_path=self.path)
            verify.assert_not_called()
    def test_ingest_excludes_old_before_download(self):
        import meteormesh_hub
        s=load_night_scope(self.path)
        old={'segment_id':'viva_20261029T145445Z_33edfe7dfbeb','sha256':'a'*64,'size_bytes':12}
        new={'segment_id':'viva_20261031T175000Z_123456789abc','sha256':'d'*64,'size_bytes':12}
        seen=[]
        class Node:
            def get_json(self,path):
                return {'segments':[old,new]} if path.endswith('segments') else {'state':'ready'}
            def pull_one(self,item,destination,**kwargs):
                seen.append(item['segment_id'])
                (destination/(item['segment_id']+'.json')).write_text(json.dumps(item))
        with patch('meteormesh_hub._mm01_verify'),             patch('meteormesh_hub.make_node',return_value=Node()),             patch('meteormesh_hub.safely_prepare_folder',return_value=self.path.parent),             patch('meteormesh_hub.MeteorNode.verify_metadata'),             patch('meteormesh_hub.verify_with_ffprobe',return_value=('INGEST_VERIFIED',{})),             patch('meteormesh_hub.store_verified_segment'):
            good,pending=meteormesh_hub.process_device({'id':'cam-viva'},self.path.parent,object(),False,scope=s)
        self.assertEqual((good,pending),(1,0))
        self.assertEqual(seen,['viva_20261031T175000Z_123456789abc'])
