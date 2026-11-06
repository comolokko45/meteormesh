import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import mm01_bridge
import repair_detector_path as repair


class FakeGuard:
    def __init__(self, root):
        self.root = Path(root)
        self._initial_dev = self.root.stat().st_dev
        self.verifications = 0
    def verify(self):
        self.verifications += 1
    def _open_parent(self, parts, create=False):
        assert not create
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts:
                nextfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=fd)
                os.close(fd)
                fd = nextfd
            return fd
        except BaseException:
            os.close(fd)
            raise


class RC12Tests(unittest.TestCase):
    def test_stable_path_maps_pinned_fd(self):
        g = SimpleNamespace(_fd=7, _mm01_pins={8:'detector-results/cam-viva'},
                            policy=SimpleNamespace(root='/srv/meteor-data'), verify=lambda: None)
        token = mm01_bridge._ACTIVE.set(g)
        try:
            p = mm01_bridge.persistent_data_path(Path('/proc/self/fd/8/a.json'))
            self.assertEqual(p, Path('/srv/meteor-data/detector-results/cam-viva/a.json'))
            with self.assertRaises(Exception):
                mm01_bridge.persistent_data_path(Path('/tmp/bad.json'))
        finally:
            mm01_bridge._ACTIVE.reset(token)

    def test_persistent_fd_without_guard_denied(self):
        with self.assertRaises(Exception):
            mm01_bridge.persistent_data_path(Path('/proc/self/fd/8/notpersistent.json'))

    def test_verify_then_repair_only_path(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            folder = root/'detector-results'/'cam-viva'
            folder.mkdir(parents=True)
            config_sha = 'a'*64
            segment = 'viva_test_1234'
            leaf = f'{segment}_{config_sha[:10]}.json'
            result = folder/leaf
            result.write_text(json.dumps({'results':[{'a':1}]}))
            sha = hashlib.sha256(result.read_bytes()).hexdigest()
            db = sqlite3.connect(':memory:')
            db.executescript('''CREATE TABLE jobs(id INTEGER PRIMARY KEY,state TEXT);
                               CREATE TABLE detector_runs(job_id INTEGER,device_id TEXT,
                                   segment_id TEXT,result_path TEXT,result_sha256 TEXT,
                                   config_sha256 TEXT);''')
            fd_path = '/proc/self/fd/8/'+leaf
            db.execute('INSERT INTO jobs VALUES (1,\'SUCCEEDED\')')
            db.execute('INSERT INTO detector_runs VALUES (1,?,?,?,?,?)',
                       ('cam-viva',segment,fd_path,sha,config_sha))
            db.commit()
            g = FakeGuard(root)
            with patch.object(repair,'ROOT',root):
                dry=repair.repair_one(db,g,'cam-viva',segment)
                self.assertEqual(dry['modified_rows'],0)
                self.assertEqual(db.execute('SELECT result_path FROM detector_runs').fetchone()[0],fd_path)
                fixed=repair.repair_one(db,g,'cam-viva',segment,apply=True)
                self.assertEqual(fixed['status'],'REPAIRED')
                self.assertEqual(db.execute('SELECT result_path FROM detector_runs').fetchone()[0],str(result))
                self.assertEqual(repair.repair_one(db,g,'cam-viva',segment,apply=True)['status'],'ALREADY_CORRECT')
            db.close()

    def test_wrong_sha_refuses_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); folder=root/'detector-results'/'cam-viva';folder.mkdir(parents=True)
            seg='viva_test_1234'; config_sha='b'*64
            leaf=f'{seg}_{config_sha[:10]}.json'
            (folder/leaf).write_text('{"results":[]}')
            db=sqlite3.connect(':memory:')
            db.executescript('''CREATE TABLE jobs(id INTEGER PRIMARY KEY,state TEXT);
                               CREATE TABLE detector_runs(job_id INTEGER,device_id TEXT,
                                   segment_id TEXT,result_path TEXT,result_sha256 TEXT,
                                   config_sha256 TEXT);''')
            old='/proc/self/fd/8/'+leaf
            db.execute("INSERT INTO jobs VALUES (1,'SUCCEEDED')")
            db.execute('INSERT INTO detector_runs VALUES (1,?,?,?,?,?)',('cam-viva',seg,old,'0'*64,config_sha))
            db.commit()
            with patch.object(repair,'ROOT',root):
                with self.assertRaisesRegex(RuntimeError,'RESULT_HASH_MISMATCH'):
                    repair.repair_one(db,FakeGuard(root),'cam-viva',seg,apply=True)
            self.assertEqual(db.execute('SELECT result_path FROM detector_runs').fetchone()[0],old)
            db.close()


if __name__=='__main__':unittest.main()
