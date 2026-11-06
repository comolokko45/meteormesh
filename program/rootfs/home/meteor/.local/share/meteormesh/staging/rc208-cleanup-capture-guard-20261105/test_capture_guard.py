import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('retention', Path(__file__).with_name('meteor-negative-video-retention.py'))
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)
DAY = dt.datetime(2026, 11, 6, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=3)))
NIGHT = dt.datetime(2026, 11, 5, 23, 0, tzinfo=dt.timezone(dt.timedelta(hours=3)))


class Response:
    status = 200
    def __init__(self, device, state):
        self.payload = json.dumps({'device_id': device, 'state': state}).encode()
    def read(self, limit):
        return self.payload[:limit]


class CaptureGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name) / 'devices.json'
        self.config.write_text(json.dumps({'devices': [
            {'id': 'cam-ruby', 'host': '192.168.0.237', 'port': 8080, 'transport': 'http_lab'},
            {'id': 'cam-surya', 'host': '192.168.0.105', 'port': 8080, 'transport': 'http_lab'},
            {'id': 'cam-viva', 'host': '192.168.0.216', 'port': 8080, 'transport': 'http_lab'}]}))
        self.states = {'192.168.0.237': ('ruby', 'idle'),
                       '192.168.0.105': ('surya', 'armed_idle'),
                       '192.168.0.216': ('viva', 'stopped')}

    def tearDown(self):
        self.temp.cleanup()

    def connection(self, host, port, timeout):
        owner = self
        class Conn:
            def request(self, *args, **kwargs): pass
            def getresponse(self):
                device, state = owner.states[host]
                return Response(device, state)
            def close(self): pass
        return Conn()

    @staticmethod
    def synced(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout='yes\n')

    def check(self, now=DAY, runner=None):
        retention.require_capture_quiet(now, config_path=self.config,
            runner=runner or self.synced, connection_factory=self.connection)

    def test_day_all_cameras_idle(self):
        self.check()

    def test_night_and_boundary_reject_without_network(self):
        for when in (NIGHT, DAY.replace(hour=18, minute=45), DAY.replace(hour=5, minute=29)):
            with self.subTest(when=when), self.assertRaisesRegex(RuntimeError, 'OUTSIDE_SAFE_DAY_WINDOW'):
                self.check(now=when, runner=lambda *a, **k: self.fail('must not probe at night'))

    def test_one_recording_or_wrong_identity_blocks(self):
        for value in (('surya', 'recording:segment'), ('wrong', 'idle'), ('surya', 'unknown')):
            self.states['192.168.0.105'] = value
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'CAMERA_IDLE_NOT_VERIFIED'):
                self.check()

    def test_missing_clock_sync_or_config_blocks(self):
        with self.assertRaisesRegex(RuntimeError, 'CLOCK_UNVERIFIED'):
            self.check(runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout='no'))
        self.config.unlink()
        with self.assertRaisesRegex(RuntimeError, 'PHONE_CONFIG_UNAVAILABLE'):
            self.check()

    def test_wrong_camera_set_blocks(self):
        self.config.write_text(json.dumps({'devices': []}))
        with self.assertRaisesRegex(RuntimeError, 'CAMERA_IDLE_NOT_VERIFIED'):
            self.check()


if __name__ == '__main__':
    unittest.main()
