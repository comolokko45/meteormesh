import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('lan_gate', Path(__file__).with_name('meteor-capture-lan-ready.py'))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class CaptureLanGateTests(unittest.TestCase):
    def test_direct_route_does_not_depend_on_tailscale(self):
        def runner(args, **kwargs):
            self.assertEqual(args[0], '/usr/sbin/ip')
            return SimpleNamespace(returncode=0, stdout=json.dumps([
                {'dst': args[-1], 'dev': 'wlan0', 'prefsrc': '192.168.0.39'}]))
        self.assertTrue(gate.direct_route_available(['192.168.0.105'], runner))

    def test_no_direct_route_or_gateway_or_tailscale_fails(self):
        for route in ({'dst': '192.168.0.105', 'dev': 'tailscale0', 'prefsrc': '100.1.1.1'},
                      {'dst': '192.168.0.105', 'dev': 'wlan0', 'gateway': '192.168.0.1', 'prefsrc': '192.168.0.39'},
                      {}):
            runner = lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps([route]))
            self.assertFalse(gate.direct_route_available(['192.168.0.105'], runner))

    def test_unreachable_first_phone_does_not_block_other_route(self):
        def runner(args, **kwargs):
            if args[-1].endswith('.105'):
                return SimpleNamespace(returncode=2, stdout='')
            return SimpleNamespace(returncode=0, stdout=json.dumps([
                {'dst': args[-1], 'dev': 'wlan0', 'prefsrc': '192.168.0.39'}]))
        self.assertTrue(gate.direct_route_available(['192.168.0.105', '192.168.0.216'], runner))

    def test_reject_unsupported_endpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'devices.json'
            config.write_text(json.dumps({'devices': [{'host': '8.8.8.8', 'port': 8080, 'transport': 'http_lab'}]}))
            with self.assertRaises(ValueError):
                gate.configured_hosts(config)


if __name__ == '__main__':
    unittest.main()
