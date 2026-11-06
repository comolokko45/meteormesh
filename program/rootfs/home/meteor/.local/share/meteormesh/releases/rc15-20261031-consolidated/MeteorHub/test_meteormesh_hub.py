import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import meteormesh_hub as hub


FP = "a" * 64
SHA = "b" * 64


class ConfigTests(unittest.TestCase):
    def write_config(self, folder, devices):
        path = Path(folder) / "devices.json"
        path.write_text(json.dumps({"devices": devices}), encoding="utf-8")
        return path

    def test_valid_three_devices(self):
        with tempfile.TemporaryDirectory() as folder:
            devices = [{"id": name, "host": "192.168.1.50", "fingerprint": FP,
                        "token_env": "METEOR_TOKEN"} for name in
                       ("cam-ruby", "cam-viva", "cam-surya")]
            self.assertEqual(len(hub.read_device_config(self.write_config(folder, devices))), 3)

    def test_deny_directory_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            devices = [{"id": "../ruby", "host": "192.168.1.50", "fingerprint": FP,
                        "token_env": "METEOR_TOKEN"}]
            with self.assertRaises(ValueError):
                hub.read_device_config(self.write_config(folder, devices))

    def test_deny_duplicate_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            device = {"id": "cam-ruby", "host": "192.168.1.50", "fingerprint": FP,
                      "token_env": "METEOR_TOKEN"}
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                hub.read_device_config(self.write_config(folder, [device, device]))

    def test_disabled_device_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            device = {"id": "cam-ruby", "enabled": False}
            self.assertEqual(hub.read_device_config(self.write_config(folder, [device])), [])


class StorageTests(unittest.TestCase):
    def test_symlink_device_folder_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data"
            path.mkdir()
            (path / "cam-ruby").symlink_to(Path(folder))
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                hub.safely_prepare_folder(path, "cam-ruby")

    def test_db_does_not_accept_changed_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            db = hub.create_database(Path(folder) / "hub.sqlite3")
            first = {"segment_id": "ruby_record_001", "size_bytes": 100, "sha256": SHA}
            hub.store_verified_segment(db, "cam-ruby", first, "INGEST_VERIFIED", {"streams": []})
            changed = dict(first, sha256="c" * 64)
            with self.assertRaisesRegex(RuntimeError, "changed"):
                hub.store_verified_segment(db, "cam-ruby", changed, "INGEST_VERIFIED", None)
            stored = db.execute("SELECT sha256 FROM segments").fetchone()
            self.assertEqual(stored[0], SHA)
            db.close()

    def test_ffprobe_absence_is_not_full_verification(self):
        with patch("meteormesh_hub.shutil.which", return_value=None):
            state, info = hub.verify_with_ffprobe(Path("not-a-video.mp4"))
        self.assertEqual(state, "HASH_VERIFIED_FFPROBE_MISSING")
        self.assertIsNone(info)

    def test_ffprobe_rejects_nonfinite_and_invalid_geometry(self):
        for duration, width, height in (("NaN", 640, 480), ("Infinity", 640, 480),
                                        ("300", 0, 480), ("0", 640, 480)):
            fake = subprocess.CompletedProcess(["ffprobe"], 0, json.dumps({
                "format": {"duration": duration},
                "streams": [{"codec_type": "video", "width": width, "height": height}]
            }), "")
            with self.subTest(duration=duration, width=width), \
                    patch("meteormesh_hub.shutil.which", return_value="ffprobe"), \
                    patch("meteormesh_hub.subprocess.run", return_value=fake):
                self.assertEqual(hub.verify_with_ffprobe(Path("video.mp4"))[0], "FFPROBE_FAILED")

    def test_ffprobe_malformed_structures_never_claim_ingest_verified(self):
        for document in ({"streams": None, "format": {"duration": "300"}},
                         {"streams": "video", "format": {"duration": "300"}},
                         {"streams": [], "format": {"duration": None}},
                         {"streams": [{"codec_type": "video"}], "format": {"duration": "300"}}):
            fake = subprocess.CompletedProcess(["ffprobe"], 0, json.dumps(document), "")
            with self.subTest(document=document), \
                    patch("meteormesh_hub.shutil.which", return_value="ffprobe"), \
                    patch("meteormesh_hub.subprocess.run", return_value=fake):
                self.assertEqual(hub.verify_with_ffprobe(Path("video.mp4"))[0], "FFPROBE_FAILED")

    def test_host_header_control_characters_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = [{"id": "cam-ruby", "host": "192.168.1.50\r\nInjected: yes",
                    "fingerprint": FP, "token_env": "METEOR_TOKEN"}]
            with self.assertRaisesRegex(ValueError, "host"):
                path = Path(folder) / "devices.json"
                path.write_text(json.dumps({"devices": bad}))
                hub.read_device_config(path)

    def test_inventory_returns_described_commands(self):
        result = hub.get_inventory()
        self.assertIn("disk_free", result["commands"])
        self.assertIn("memory", result["commands"])
        self.assertEqual(result["schema"], 1)

    def test_one_phone_sync_marks_only_verified_mp4(self):
        segment = {"segment_id": "ruby_record_001", "sha256": SHA, "size_bytes": 3}

        class FakeNode:
            def get_json(self, path):
                if path.endswith("/health"):
                    return {"state": "idle", "free_bytes": 10000}
                return {"segments": [segment]}

            def pull_one(self, item, destination):
                (destination / "ruby_record_001.mp4").write_bytes(b"MP4")
                (destination / "ruby_record_001.json").write_text(json.dumps(item))
                return "downloaded"

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = hub.create_database(root / "meteorhub.sqlite3")
            with patch("meteormesh_hub.make_node", return_value=FakeNode()), \
                 patch("meteormesh_hub.verify_with_ffprobe",
                       return_value=("INGEST_VERIFIED", {"format": {"duration": "300"}})):
                good, pending = hub.process_device({"id": "cam-ruby"}, root, db, False)
            self.assertEqual((good, pending), (1, 0))
            state = db.execute("SELECT state FROM segments WHERE segment_id='ruby_record_001'").fetchone()[0]
            self.assertEqual(state, "INGEST_VERIFIED")
            db.close()

    def test_ffprobe_error_keeps_segment_pending(self):
        segment = {"segment_id": "ruby_record_001", "sha256": SHA, "size_bytes": 3}

        class FakeNode:
            def get_json(self, path):
                return ({"state": "idle"} if path.endswith("/health")
                        else {"segments": [segment]})

            def pull_one(self, item, destination):
                (destination / "ruby_record_001.mp4").write_bytes(b"MP4")
                (destination / "ruby_record_001.json").write_text(json.dumps(item))
                return "downloaded"

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = hub.create_database(root / "meteorhub.sqlite3")
            with patch("meteormesh_hub.make_node", return_value=FakeNode()), \
                 patch("meteormesh_hub.verify_with_ffprobe",
                       return_value=("FFPROBE_FAILED", {"error": "broken file"})):
                good, pending = hub.process_device({"id": "cam-ruby"}, root, db, False)
            self.assertEqual((good, pending), (0, 1))
            state = db.execute("SELECT state FROM segments").fetchone()[0]
            self.assertEqual(state, "FFPROBE_FAILED")
            db.close()

    def test_hub_refuses_db_ingest_when_video_lacks_sidecar(self):
        segment = {"segment_id": "ruby_record_001", "sha256": SHA, "size_bytes": 3}

        class BrokenNode:
            def get_json(self, path):
                return ({"state": "idle"} if path.endswith("/health")
                        else {"segments": [segment]})

            def pull_one(self, item, destination):
                (destination / "ruby_record_001.mp4").write_bytes(b"MP4")
                return "already verified"  # Deliberately simulates old downloader's bug.

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = hub.create_database(root / "meteorhub.sqlite3")
            with patch("meteormesh_hub.make_node", return_value=BrokenNode()):
                good, pending = hub.process_device({"id": "cam-ruby"}, root, db, False)
            self.assertEqual((good, pending), (0, 1))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM segments").fetchone()[0], 0)
            db.close()


if __name__ == "__main__":
    unittest.main()
