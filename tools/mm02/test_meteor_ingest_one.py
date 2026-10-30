"""MM-02 bounded ingest safety and repeatability tests; all offline."""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meteor_ingest_one import ingest_one, select_segment, enqueue_selected
from meteornode_pull import MeteorNode
from meteormesh_pipeline import database_for


class FakeNode:
    def __init__(self, content=b'FAKE_TEST_VIDEO'):
        self.content = content
        self.calls = []
        self.segment = {'segment_id': 'viva_test_001', 'size_bytes': len(content),
                        'sha256': hashlib.sha256(content).hexdigest(), 'state': 'READY'}

    def get_json(self, path):
        self.calls.append(path)
        if path.endswith('health'):
            return {'node_version': '0.10.TEST'}
        return {'segments': [self.segment]}

    def pull_one(self, segment, destination, min_free_bytes=0):
        self.calls.append('pull')
        video = destination / (segment['segment_id'] + '.mp4')
        sidecar = destination / (segment['segment_id'] + '.json')
        if video.exists():
            return 'already verified + sidecar reconciled'
        video.write_bytes(self.content)
        sidecar.write_text(json.dumps(segment), encoding='utf8')
        return 'downloaded + SHA256 verified'


class BoundedIngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='meteormesh-mm02-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'data'
        self.root.mkdir()
        self.config = Path(self.tmp.name) / 'detector.json'
        self.config.write_text('{"test":true}')
        self.env = patch.dict(os.environ, {'METEORMESH_ISOLATED_TEST_ROOT': 'isolated-only'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_largest_ready_selected(self):
        base = FakeNode().segment
        inventory = {'segments': [dict(base, segment_id='viva_short', size_bytes=10),
                                  dict(base, segment_id='viva_long', size_bytes=300),
                                  dict(base, segment_id='viva_bad', size_bytes=500, state='RECORDING')]}
        self.assertEqual(select_segment(inventory, min_size=20)['segment_id'], 'viva_long')

    def test_reject_invalid_inventory(self):
        for inventory in ({}, {'segments': {}}, {'segments': [None]}):
            with self.assertRaises((ValueError, RuntimeError)):
                select_segment(inventory, min_size=1)

    def test_specific_must_exist(self):
        with self.assertRaisesRegex(RuntimeError, 'NO_ELIGIBLE_READY_SEGMENT'):
            select_segment({'segments': [FakeNode().segment]}, specific='viva_other', min_size=1)

    def test_production_without_mount_refuses_before_phone(self):
        node = FakeNode()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'MOUNT_NOT_VERIFIED'):
                ingest_one(node, root=self.root, device_id='cam-viva',
                           detector_config=self.config, minimum_bytes=1)
        self.assertEqual(node.calls, [])

    def test_verified_ingest_queued_and_repeat_idempotent(self):
        node = FakeNode()
        with patch('meteor_ingest_one.verify_with_ffprobe', return_value=('INGEST_VERIFIED', {})):
            first = ingest_one(node, root=self.root, device_id='cam-viva',
                               detector_config=self.config, minimum_bytes=1)
            second = ingest_one(node, root=self.root, device_id='cam-viva',
                                detector_config=self.config, minimum_bytes=1)
        self.assertEqual(first['status'], 'VERIFIED_AND_QUEUED')
        self.assertEqual(first['jobs_new_total'], 1)
        self.assertEqual(second['jobs_new_total'], 0)
        self.assertEqual(second['queue']['jobs'], {'WAITING': 1})
        self.assertEqual(first['phone_deleted'], False)
        with database_for(self.root) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)

    def test_only_chosen_video_is_queued(self):
        node = FakeNode()
        with patch('meteor_ingest_one.verify_with_ffprobe', return_value=('INGEST_VERIFIED', {})):
            ingest_one(node, root=self.root, device_id='cam-viva',
                       detector_config=self.config, minimum_bytes=1)
        with database_for(self.root) as db:
            db.execute("INSERT INTO segments VALUES (?,?,?,?,?,?,?)",
                ('cam-ruby', 'ruby_other', 'a'*64, 200, 'INGEST_VERIFIED', '{}', 'now'))
            db.commit()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT device_id FROM jobs").fetchone()[0], 'cam-viva')

    def test_failed_media_does_not_enqueue(self):
        node = FakeNode()
        with patch('meteor_ingest_one.verify_with_ffprobe', return_value=('FFPROBE_FAILED', {})):
            with self.assertRaisesRegex(RuntimeError, 'FFPROBE_REJECTED'):
                ingest_one(node, root=self.root, device_id='cam-viva',
                           detector_config=self.config, minimum_bytes=1)
        with database_for(self.root) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)

    def test_unsafe_device_name_fails_before_phone(self):
        node = FakeNode()
        with self.assertRaisesRegex(ValueError, 'camera'):
            ingest_one(node, root=self.root, device_id='../etc',
                       detector_config=self.config, minimum_bytes=1)
        self.assertEqual(node.calls, [])

    def test_symlinked_device_folder_refused(self):
        node = FakeNode()
        (self.root / 'cam-viva').symlink_to(Path(self.tmp.name))
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            ingest_one(node, root=self.root, device_id='cam-viva',
                       detector_config=self.config, minimum_bytes=1)
        self.assertNotIn('pull', node.calls)

    def test_token_omitted_not_accepted(self):
        from meteor_ingest_one import main
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as error:
                main(['--host', '192.168.0.216', '--detector-config', str(self.config)])
        self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
