import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

import test_negative_retention_safety as fixtures

retention = fixtures.retention


class GuardIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NegativeRetentionSafetyTests(methodName='runTest')
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_recording_started_before_unlink_preserves_file_and_recovers_later(self):
        target = None
        for stamp in ('20261104T184500Z', '20261104T185000Z', '20261104T185500Z'):
            for camera in sorted(retention.CAMERAS):
                segment, video = self.fixture.add_job(camera, stamp)
                if camera == 'cam-ruby' and stamp == '20261104T185000Z':
                    target = (segment, video)
        self.assertIsNotNone(target)
        args = ['--apply', '--max-deletes', '1', '--only-device', 'cam-ruby', '--only-segment', target[0]]
        with patch.object(retention, 'require_capture_quiet', side_effect=[None, None,
                RuntimeError('CLEANUP_CAMERA_IDLE_NOT_VERIFIED')]):
            with self.assertRaisesRegex(RuntimeError, 'CAMERA_IDLE_NOT_VERIFIED'):
                self.fixture.run_main(args)
        self.assertTrue(target[1].is_file())
        with closing(sqlite3.connect(self.fixture.db_path)) as db:
            disposition = db.execute('SELECT disposition FROM source_dispositions WHERE segment_id=?',
                                     (target[0],)).fetchone()
        self.assertEqual(disposition, ('DELETE_PENDING',))
        with patch.object(retention, 'require_capture_quiet', return_value=None):
            code, output = self.fixture.run_main(args)
        self.assertEqual(code, 0)
        self.assertFalse(target[1].exists())
        self.assertIn('RETENTION_DELETED', output)


if __name__ == '__main__':
    unittest.main()
