"""Offline HTTP 8080 smoke tests: local loopback only, no Android data touched."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from meteornode_http_pull import MeteorNode

TOKEN = 'testtoken' * 8
PAYLOAD = b'not-a-real-mp4-test-only' * 1000
SHA = hashlib.sha256(PAYLOAD).hexdigest()
SEG = {
    'segment_id': 'viva_test_01',
    'state': 'ready',
    'size_bytes': len(PAYLOAD),
    'sha256': SHA,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == '/api/v1/health':
            output = json.dumps({'device_id': 'viva', 'state': 'idle'}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(output)))
            self.end_headers()
            self.wfile.write(output)
            return
        if self.headers.get('Authorization') != f'Bearer {TOKEN}':
            self.send_response(401)
            self.end_headers()
            return
        if self.path == '/api/v1/segments':
            output = json.dumps({'segments': [SEG]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(output)))
            self.end_headers()
            self.wfile.write(output)
            return
        if self.path.endswith('/file'):
            start = 0
            range_value = self.headers.get('Range')
            if range_value:
                start = int(range_value.replace('bytes=', '').split('-')[0])
            data = PAYLOAD[start:]
            self.send_response(206 if range_value else 200)
            self.send_header('Content-Length', str(len(data)))
            if range_value:
                self.send_header('Content-Range', f'bytes {start}-{len(PAYLOAD)-1}/{len(PAYLOAD)}')
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.end_headers()


class HttpPullTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def node(self, key=TOKEN):
        return MeteorNode('127.0.0.1', self.server.server_port, key)

    def test_health(self):
        self.assertEqual('viva', self.node().get_json('/api/v1/health')['device_id'])

    def test_unauthorized(self):
        with self.assertRaises(RuntimeError):
            self.node('x' * 40).get_json('/api/v1/segments')

    def test_verified_download_and_idempotence(self):
        with TemporaryDirectory() as td:
            target = Path(td)
            self.assertIn('downloaded', self.node().pull_one(SEG, target))
            self.assertEqual(hashlib.sha256((target / 'viva_test_01.mp4').read_bytes()).hexdigest(), SHA)
            self.assertIn('already verified', self.node().pull_one(SEG, target))
            self.assertTrue((target / 'viva_test_01.json').is_file())

    def test_range_resume(self):
        with TemporaryDirectory() as td:
            target = Path(td)
            (target / 'viva_test_01.mp4.part').write_bytes(PAYLOAD[:431])
            self.assertIn('verified', self.node().pull_one(SEG, target))
            self.assertEqual((target / 'viva_test_01.mp4').read_bytes(), PAYLOAD)

    def test_bad_existing_video_never_overwritten(self):
        with TemporaryDirectory() as td:
            target = Path(td)
            (target / 'viva_test_01.mp4').write_bytes(b'keep-me')
            with self.assertRaises(RuntimeError):
                self.node().pull_one(SEG, target)
            self.assertEqual((target / 'viva_test_01.mp4').read_bytes(), b'keep-me')


if __name__ == '__main__':
    unittest.main()
