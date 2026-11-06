"""Focused release checks, no network, HDD access, or production writes."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import meteormesh_hub as h

class MergeChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mm03-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'cam-viva'
        self.folder.mkdir()
        self.segments = [
            {'segment_id':f'viva_20261029T15{i:04d}Z_abcdefgh1234',
             'size_bytes':2000, 'sha256':'a'*64} for i in range(3)]
        self.partial_id = self.segments[1]['segment_id']
        (self.folder / (self.partial_id+'.mp4.part')).write_bytes(b'test')

    def test_partial_first_with_one_limit(self):
        class FakeNode:
            def get_json(_self, path):
                if path.endswith('health'):return {'state':'idle'}
                return {'segments':self.segments}
            def pull_one(_self, segment, *args, **kwargs):
                self.selected.append(segment['segment_id'])
                raise RuntimeError('simulated no download')
        self.selected=[]
        with patch.object(h, '_mm01_verify'), patch.object(h, 'make_node', return_value=FakeNode()), \
             patch.object(h, 'safely_prepare_folder', return_value=self.folder):
            good, pending = h.process_device({'id':'cam-viva'}, self.root,
                                              object(), False, max_segments=1)
        self.assertEqual(self.selected,[self.partial_id])
        self.assertEqual((good,pending),(0,3))  # one failed + two intentionally deferred

    def test_existing_mp4_is_selected_before_new_download(self):
        (self.folder / (self.partial_id+'.mp4.part')).unlink()
        completed_id = self.segments[2]['segment_id']
        (self.folder / (completed_id+'.mp4')).write_bytes(b'completed from previous run')
        class FakeNode:
            def get_json(_self, path):
                return {'state':'idle'} if path.endswith('health') else {'segments':self.segments}
            def pull_one(_self, segment, *args, **kwargs):
                self.selected.append(segment['segment_id'])
                raise RuntimeError('simulated no download')
        self.selected=[]
        with patch.object(h, '_mm01_verify'), patch.object(h, 'make_node', return_value=FakeNode()), \
             patch.object(h, 'safely_prepare_folder', return_value=self.folder):
            h.process_device({'id':'cam-viva'}, self.root, object(), False, max_segments=1)
        self.assertEqual(self.selected, [completed_id])

    def test_limits_fail_closed(self):
        class FakeNode:
            def get_json(_self,path):return {'segments':self.segments} if path.endswith('segments') else {}
        with patch.object(h,'_mm01_verify'), patch.object(h,'make_node',return_value=FakeNode()),\
             patch.object(h,'safely_prepare_folder', return_value=self.folder):
            for bad in (0,-1,101):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    h.process_device({'id':'cam-viva'},self.root, object(),False,max_segments=bad)

    def test_lab_endpoint_rejects_outside_private_range(self):
        from meteornode_pull import require_lab_endpoint
        for host,port in (('8.8.8.8',8080),('192.168.0.216',443),('example.com',8080)):
            with self.subTest(host=host),self.assertRaises(ValueError):
                require_lab_endpoint(host,port)
        require_lab_endpoint('192.168.0.216',8080)

if __name__ == '__main__':unittest.main()
