"""Offline-only regression for Node 0.10 HTTP laboratory adapter."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import http.client
from meteormesh_hub import read_device_config, make_node
from meteornode_pull import MeteorNode, MeteorNodeLabHTTP

class LabAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='meteormesh-http-adapter-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def load(self,extra=None):
        x={'id':'viva','host':'192.168.0.216','port':8080,'transport':'http_lab','token_env':'METEOR_TEST_TOKEN'}
        x.update(extra or {})
        p=self.root/'devices.json';p.write_text(json.dumps({'devices':[x]}))
        return read_device_config(p)
    def test_accepts_real_viva_config(self):
        d=self.load(); self.assertEqual(d[0]['transport'],'http_lab');self.assertIsNone(d[0]['fingerprint'])
    def test_safely_builds_http_node(self):
        with patch.dict(os.environ,{'METEOR_TEST_TOKEN':'t'*32}):
            node=make_node(self.load()[0]);self.assertIsInstance(node,MeteorNodeLabHTTP)
            self.assertIsInstance(node.connection(),http.client.HTTPConnection)
            self.assertNotIsInstance(node.connection(),http.client.HTTPSConnection)
    def test_no_token_does_not_fallback_to_embedded_token(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,'Missing pairing'):
                make_node(self.load()[0])
    def test_invalid_lab_hosts_rejected(self):
        for host in ('8.8.8.8','127.0.0.1','1.2.3.4','0.0.0.0','169.254.1.1','google.com','192.168.0.216:8080','192.168.0.216\r\nHost: x'):
            with self.subTest(host=host),self.assertRaises(ValueError):
                self.load({'host':host})
    def test_invalid_lab_port_rejected(self):
        for port in (22,80,443,8443,0,9999,65536):
            with self.subTest(port=port),self.assertRaises(ValueError):
                self.load({'port':port})
    def test_invalid_lab_fingerprint_rejected(self):
        with self.assertRaisesRegex(ValueError,'must not supply'):
            self.load({'fingerprint':'a'*64})
    def test_unknown_transport_rejected(self):
        with self.assertRaisesRegex(ValueError,'Unsupported transport'):
            self.load({'transport':'http'})
    def test_tls_pinned_remains_default(self):
        ds=self.load({'transport':'tls_pinned','port':8443,'fingerprint':'a'*64})
        with patch.dict(os.environ,{'METEOR_TEST_TOKEN':'t'*32}):
            self.assertEqual(type(make_node(ds[0])),MeteorNode)
    def test_tampered_tls_fingerprint_still_rejected(self):
        with self.assertRaisesRegex(ValueError,'fingerprint'):
            self.load({'transport':'tls_pinned','port':8443,'fingerprint':None})
    def test_lab_http_get_auth_protocol(self):
        node=MeteorNodeLabHTTP('192.168.0.216',8080,'t'*32)
        class FakeResponse:
            status=200
            def read(self,limit):return b'{"segments":[]}'
        class FakeConnection:
            def __init__(self):self.calls=[]
            def request(self,*a,**k):self.calls.append((a,k))
            def getresponse(self):return FakeResponse()
            def close(self):pass
        fake=FakeConnection()
        with patch.object(node,'connection',return_value=fake):
            self.assertEqual(node.get_json('/api/v1/segments'),{'segments':[]})
        self.assertEqual(fake.calls[0][0][:2],('GET','/api/v1/segments'))
        self.assertEqual(fake.calls[0][1]['headers']['Authorization'],'Bearer '+'t'*32)
    def test_lab_http_inherits_mm01_write_guard(self):
        node=MeteorNodeLabHTTP('192.168.0.216',8080,'t'*32)
        with self.assertRaisesRegex(RuntimeError,'LAB_HTTP_WRITE_REQUIRES_VERIFIED_GUARD'):
            node.pull_one({'segment_id':'viva_demo_001','size_bytes':100,'sha256':'b'*64},Path('/opt/meteormesh/test/unguarded'))

if __name__=='__main__':unittest.main()
