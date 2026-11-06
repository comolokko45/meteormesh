import unittest
import hashlib
import json
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from meteornode_pull import normalize_fingerprint, VALID_ID, MeteorNode


class FakeResponse:
    def __init__(self, body, status, range_header=None):
        self.body = body
        self.status = status
        self.range_header = range_header
        self.index = 0

    def getheader(self, name, default=""):
        return self.range_header if name == "Content-Range" else default

    def read(self, limit=1_048_576):
        chunk = self.body[self.index : self.index + limit]
        self.index += len(chunk)
        return chunk


class FakeConnection:
    def __init__(self, body):
        self.body = body
        self.headers = {}
        self.response = None

    def request(self, _method, _path, headers=None):
        self.headers = headers or {}
        resume = self.headers.get("Range")
        offset = int(resume.removeprefix("bytes=").removesuffix("-")) if resume else 0
        self.response = FakeResponse(self.body[offset:], 206 if resume else 200,
                                     f"bytes {offset}-{len(self.body)-1}/{len(self.body)}")

    def getresponse(self):
        return self.response

    def close(self):
        pass


class FakeNode(MeteorNode):
    def __init__(self, body):
        super().__init__("127.0.0.1", 8443, "aa" * 32, "x" * 64)
        self.body = body
        self.last_connection = None

    def connection(self):
        self.last_connection = FakeConnection(self.body)
        return self.last_connection


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.body = (b"METEOR DATA\x00" * 20000)
        self.segment = {
            "segment_id": "ruby_20261026_0001",
            "size_bytes": len(self.body),
            "sha256": hashlib.sha256(self.body).hexdigest(),
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_initial_download_is_verified(self):
        node = FakeNode(self.body)
        self.assertIn("SHA256 verified", node.pull_one(self.segment, self.folder))
        self.assertEqual((self.folder / "ruby_20261026_0001.mp4").read_bytes(), self.body)
        self.assertFalse((self.folder / "ruby_20261026_0001.mp4.part").exists())
        self.assertEqual(json.loads((self.folder / "ruby_20261026_0001.json").read_text()), self.segment)

    def test_resume_from_partial(self):
        partial = self.folder / "ruby_20261026_0001.mp4.part"
        partial.write_bytes(self.body[:2048])
        node = FakeNode(self.body)
        node.pull_one(self.segment, self.folder)
        self.assertEqual(node.last_connection.headers.get("Range"), "bytes=2048-")
        self.assertEqual((self.folder / "ruby_20261026_0001.mp4").read_bytes(), self.body)

    def test_low_disk_reserve_fails_before_network_and_preserves_source(self):
        node = FakeNode(self.body)
        with patch("meteornode_pull.shutil.disk_usage",
                   return_value=SimpleNamespace(free=len(self.body) + 1024)):
            with self.assertRaisesRegex(RuntimeError, "Not enough disk space"):
                node.pull_one(self.segment, self.folder, min_free_bytes=2048)
        self.assertIsNone(node.last_connection)
        self.assertFalse((self.folder / "ruby_20261026_0001.mp4.part").exists())

    def test_wrong_hash_never_becomes_ready(self):
        node = FakeNode(self.body)
        altered = dict(self.segment, sha256="0" * 64)
        with self.assertRaises(RuntimeError):
            node.pull_one(altered, self.folder)
        self.assertFalse((self.folder / "ruby_20261026_0001.mp4").exists())

    def test_existing_unexpected_file_is_not_overwritten(self):
        file = self.folder / "ruby_20261026_0001.mp4"
        file.write_bytes(b"preexisting important data")
        node = FakeNode(self.body)
        with self.assertRaises(RuntimeError):
            node.pull_one(self.segment, self.folder)
        self.assertEqual(file.read_bytes(), b"preexisting important data")

    def test_partial_symlink_is_never_written(self):
        important = self.folder / "important.txt"
        important.write_text("DO NOT CHANGE")
        (self.folder / "ruby_20261026_0001.mp4.part").symlink_to(important)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            FakeNode(self.body).pull_one(self.segment, self.folder)
        self.assertEqual(important.read_text(), "DO NOT CHANGE")

    def test_conflicting_metadata_is_not_overwritten(self):
        path = self.folder / "ruby_20261026_0001.json"
        path.write_text(json.dumps(dict(self.segment, sha256="f" * 64)))
        with self.assertRaisesRegex(RuntimeError, "Metadata conflict"):
            FakeNode(self.body).pull_one(self.segment, self.folder)
        self.assertFalse((self.folder / "ruby_20261026_0001.mp4").exists())

    def test_completed_mp4_without_sidecar_is_reconciled_after_crash(self):
        video = self.folder / "ruby_20261026_0001.mp4"
        video.write_bytes(self.body)
        result = FakeNode(self.body).pull_one(self.segment, self.folder)
        self.assertIn("sidecar reconciled", result)
        self.assertEqual(json.loads(video.with_suffix(".json").read_text()), self.segment)

    def test_completed_mp4_with_conflicting_sidecar_is_never_accepted(self):
        video = self.folder / "ruby_20261026_0001.mp4"
        video.write_bytes(self.body)
        metadata = video.with_suffix(".json")
        metadata.write_text(json.dumps(dict(self.segment, segment_id="DIFFERENT")))
        with self.assertRaisesRegex(RuntimeError, "Metadata conflict"):
            FakeNode(self.body).pull_one(self.segment, self.folder)
        self.assertEqual(video.read_bytes(), self.body)
        self.assertEqual(json.loads(metadata.read_text())["segment_id"], "DIFFERENT")

    def test_crash_during_video_promotion_recovers_on_next_run(self):
        partial = self.folder / "ruby_20261026_0001.mp4.part"
        partial.write_bytes(self.body)
        node = FakeNode(self.body)
        real_replace = __import__("os").replace

        def fail_video_promotion(source, target):
            if source == partial:
                raise OSError("Simulated power loss")
            return real_replace(source, target)

        with patch("meteornode_pull.os.replace", side_effect=fail_video_promotion):
            with self.assertRaisesRegex(OSError, "Simulated power loss"):
                node.pull_one(self.segment, self.folder)
        self.assertTrue((self.folder / "ruby_20261026_0001.json").is_file())
        self.assertFalse((self.folder / "ruby_20261026_0001.mp4").exists())
        self.assertIn("SHA256 verified", node.pull_one(self.segment, self.folder))
        self.assertEqual((self.folder / "ruby_20261026_0001.mp4").read_bytes(), self.body)

    def test_valid_staged_metadata_recovers_without_overwriting(self):
        staged = self.folder / "ruby_20261026_0001.json.part"
        staged.write_text(json.dumps(self.segment))
        video = self.folder / "ruby_20261026_0001.mp4"
        video.write_bytes(self.body)
        FakeNode(self.body).pull_one(self.segment, self.folder)
        self.assertFalse(staged.exists())
        self.assertEqual(json.loads(video.with_suffix(".json").read_text()), self.segment)

    def test_conflicting_staged_metadata_is_preserved(self):
        staged = self.folder / "ruby_20261026_0001.json.part"
        staged.write_text("{incomplete")
        with self.assertRaisesRegex(RuntimeError, "Metadata conflict"):
            FakeNode(self.body).pull_one(self.segment, self.folder)
        self.assertEqual(staged.read_text(), "{incomplete")
        self.assertFalse((self.folder / "ruby_20261026_0001.mp4").exists())


class SafetyTests(unittest.TestCase):
    def test_fingerprint_normalization(self):
        fingerprint = "AA:" * 31 + "AA"
        self.assertEqual(normalize_fingerprint(fingerprint), "aa" * 32)

    def test_bad_fingerprints_rejected(self):
        for value in ("", "00", "xyz" * 30, "gg" * 32):
            with self.assertRaises(ValueError):
                normalize_fingerprint(value)

    def test_segment_ids_cannot_escape_directory(self):
        self.assertIsNotNone(VALID_ID.fullmatch("ruby_20261026_0001"))
        for candidate in ("../etc/passwd", "../../data", "foo/bar", "..", "a\\b"):
            self.assertIsNone(VALID_ID.fullmatch(candidate))


if __name__ == "__main__":
    unittest.main()
