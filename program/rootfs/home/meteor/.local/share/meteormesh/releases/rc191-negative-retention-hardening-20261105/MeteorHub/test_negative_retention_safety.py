from __future__ import annotations

import contextlib
from contextlib import closing
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("meteor-negative-video-retention.py")
fcntl_stub = sys.modules.setdefault("fcntl", ModuleType("fcntl"))
fcntl_stub.LOCK_EX = 2
fcntl_stub.LOCK_NB = 4
fcntl_stub.flock = lambda *_args: None
SPEC = importlib.util.spec_from_file_location("negative_retention_rc191", SCRIPT)
retention = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retention)
WRAPPER_SCRIPT = Path(__file__).with_name("after_analysis_with_backup.py")
if not WRAPPER_SCRIPT.is_file():
    WRAPPER_SCRIPT = SCRIPT.parent.parent / "after_analysis_with_backup.py"
WRAPPER_SPEC = importlib.util.spec_from_file_location("retention_wrapper_rc191", WRAPPER_SCRIPT)
wrapper = importlib.util.module_from_spec(WRAPPER_SPEC)
WRAPPER_SPEC.loader.exec_module(wrapper)


class NegativeRetentionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.data = root / "data"
        self.data.mkdir()
        self.state = root / "state"
        self.state.mkdir()
        self.state.chmod(0o700)
        for camera in retention.CAMERAS:
            (self.data / camera).mkdir()
            (self.data / "detector-results" / camera).mkdir(parents=True)
        (self.data / ".meteor-morning.lock").write_text("", encoding="utf-8")
        self.db_path = self.data / "meteorhub.sqlite3"
        self.activation = self.state / "negative-video-retention-enabled.json"
        self.activation.write_text(json.dumps({
            "schema": "meteormesh.negative-retention-activation.v1",
            "activation_utc": "2026-11-04T18:32:12+00:00",
        }), encoding="utf-8")
        self.old = {name: getattr(retention, name) for name in
                    ("DATA", "DB", "STATE", "ACTIVATION", "RECEIPTS", "verify_hdd")}
        retention.DATA = self.data
        retention.DB = self.db_path
        retention.STATE = self.state
        retention.ACTIVATION = self.activation
        retention.RECEIPTS = self.state / "negative-video-retention.jsonl"
        retention.verify_hdd = lambda: None
        with closing(sqlite3.connect(self.db_path)) as db:
            with db:
                db.executescript("""
                CREATE TABLE segments (
                    device_id TEXT, segment_id TEXT, sha256 TEXT,
                    size_bytes INTEGER, state TEXT, ffprobe_json TEXT,
                    verified_utc TEXT,
                    PRIMARY KEY(device_id,segment_id)
                );
                CREATE TABLE jobs (
                    id INTEGER PRIMARY KEY, device_id TEXT, segment_id TEXT,
                    video_sha256 TEXT, finished_utc TEXT, state TEXT
                );
                CREATE TABLE detector_runs (
                    job_id INTEGER, input_sha256 TEXT, result_path TEXT,
                    result_sha256 TEXT
                );
                """)

    def tearDown(self):
        for name, value in self.old.items():
            setattr(retention, name, value)
        self.temp.cleanup()

    def add_job(self, camera: str, timestamp: str, result_items=None, state="SUCCEEDED"):
        segment = f"{camera.removeprefix('cam-')}_{timestamp}_abcdef012345"
        video_bytes = ("video:" + segment).encode("ascii")
        video_sha = hashlib.sha256(video_bytes).hexdigest()
        video = self.data / camera / (segment + ".mp4")
        video.write_bytes(video_bytes)
        sidecar = {
            "segment_id": segment,
            "device_id": camera.removeprefix("cam-"),
            "sha256": video_sha,
            "size_bytes": len(video_bytes),
        }
        (self.data / camera / (segment + ".json")).write_text(
            json.dumps(sidecar), encoding="utf-8")
        result_path = self.data / "detector-results" / camera / (segment + "_result.json")
        result_bytes = json.dumps({"version": "test-v1", "results": result_items or []},
                                  separators=(",", ":")).encode("utf-8")
        result_path.write_bytes(result_bytes)
        result_sha = hashlib.sha256(result_bytes).hexdigest()
        with closing(sqlite3.connect(self.db_path)) as db, db:
            cursor = db.execute(
                "INSERT INTO jobs(device_id,segment_id,video_sha256,finished_utc,state) "
                "VALUES(?,?,?,?,?)",
                (camera, segment, video_sha, "2026-11-04T19:00:00+00:00", state),
            )
            job_id = cursor.lastrowid
            db.execute(
                "INSERT INTO segments(device_id,segment_id,sha256,size_bytes,state,verified_utc) "
                "VALUES(?,?,?,?,?,?)",
                (camera, segment, video_sha, len(video_bytes), "INGEST_VERIFIED",
                 "2026-11-04T18:59:00+00:00"),
            )
            if state == "SUCCEEDED":
                db.execute(
                    "INSERT INTO detector_runs(job_id,input_sha256,result_path,result_sha256) "
                    "VALUES(?,?,?,?)",
                    (job_id, video_sha, str(result_path), result_sha),
                )
        return segment, video

    def add_camera_coverage(self, timestamp: str, skip=("cam-ruby",)):
        for camera in sorted(retention.CAMERAS - set(skip)):
            segment, _video = self.add_job(camera, timestamp)
            with closing(sqlite3.connect(self.db_path)) as db, db:
                db.execute("UPDATE jobs SET finished_utc=? WHERE segment_id=?",
                           ("2026-11-04T18:30:00+00:00", segment))

    def run_main(self, args=None):
        output = io.StringIO()
        with patch.object(retention.fcntl, "flock", lambda *_args: None), \
                contextlib.redirect_stdout(output):
            result = retention.main(args)
        return result, output.getvalue()

    def test_default_invocation_is_read_only_dry_run(self):
        first = self.add_job("cam-ruby", "20261104T184000Z")
        self.add_job("cam-ruby", "20261104T185000Z")
        self.add_job("cam-ruby", "20261104T190000Z")
        for timestamp in ("20261104T184000Z", "20261104T185000Z", "20261104T190000Z"):
            self.add_camera_coverage(timestamp)
        code, output = self.run_main([])
        self.assertEqual(code, 0)
        self.assertIn('"mode": "DRY_RUN"', output)
        self.assertIn('"eligible": 3', output)
        self.assertIn('"deleted": 0', output)
        self.assertTrue(first[1].is_file())
        with closing(sqlite3.connect(self.db_path)) as db, db:
            self.assertIsNone(db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_dispositions'"
            ).fetchone())

    def test_exact_segment_review_is_scoped_and_read_only(self):
        target = self.add_job("cam-ruby", "20261104T184000Z")
        other = self.add_job("cam-ruby", "20261104T190000Z")
        self.add_camera_coverage("20261104T184000Z")
        code, output = self.run_main([
            "--dry-run", "--only-device", "cam-ruby", "--only-segment", target[0],
        ])
        self.assertEqual(code, 0)
        self.assertIn('"eligible": 1', output)
        self.assertIn(target[0], output)
        self.assertNotIn(other[0], output)
        self.assertTrue(target[1].is_file())
        self.assertTrue(other[1].is_file())

    def test_cross_camera_candidate_protects_related_negative(self):
        _candidate, _ = self.add_job("cam-surya", "20261104T184000Z", [{"id": "event-1"}])
        _negative, negative_path = self.add_job("cam-ruby", "20261104T184300Z")
        code, output = self.run_main(["--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn('"candidate_preserved": 1', output)
        self.assertIn('"adjacent_preserved": 1', output)
        self.assertIn('"eligible": 0', output)
        self.assertTrue(negative_path.is_file())

    def test_unreadable_success_result_protects_all_camera_neighbors(self):
        _candidate, _ = self.add_job("cam-viva", "20261104T184000Z", [{"id": "event"}])
        _negative, negative_path = self.add_job("cam-ruby", "20261104T184300Z")
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("UPDATE detector_runs SET result_sha256=? WHERE job_id=1", ("0" * 64,))
        code, output = self.run_main(["--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn('"unreadable_successful_results_protected": 1', output)
        self.assertIn('"adjacent_preserved": 1', output)
        self.assertTrue(negative_path.is_file())

    def test_pending_neighbor_analysis_blocks_deletion(self):
        _negative, video = self.add_job("cam-ruby", "20261104T184000Z")
        segment = "surya_20261104T184300Z_pending01"
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute(
                "INSERT INTO jobs(device_id,segment_id,video_sha256,finished_utc,state) "
                "VALUES(?,?,?,?,?)",
                ("cam-surya", segment, "0" * 64, "2026-11-04T19:00:00+00:00", "WAITING"),
            )
        code, output = self.run_main(["--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn('"analysis_pending": 1', output)
        self.assertIn('"eligible": 0', output)
        self.assertTrue(video.is_file())

    def test_missing_camera_coverage_blocks_negative_deletion(self):
        _negative, video = self.add_job("cam-ruby", "20261104T184000Z")
        code, output = self.run_main(["--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn('"camera_coverage_incomplete": 1', output)
        self.assertIn('"eligible": 0', output)
        self.assertTrue(video.is_file())

    def test_successful_job_without_detector_run_is_global_uncertainty(self):
        segment, _video = self.add_job("cam-ruby", "20261104T184000Z")
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("DELETE FROM detector_runs")
        db = retention.open_db(read_only=True)
        try:
            protected, unreadable = retention.candidate_times(db)
        finally:
            db.close()
        self.assertEqual(unreadable, 1)
        self.assertEqual(protected, [retention.segment_epoch(segment)])

    def test_apply_mode_caps_deletions_and_keeps_sidecars(self):
        first = self.add_job("cam-ruby", "20261104T184000Z")
        second = self.add_job("cam-ruby", "20261104T185000Z")
        third = self.add_job("cam-ruby", "20261104T190000Z")
        for timestamp in ("20261104T184000Z", "20261104T185000Z", "20261104T190000Z"):
            self.add_camera_coverage(timestamp)
        code, output = self.run_main([
            "--apply", "--max-deletes", "1", "--only-device", "cam-ruby",
            "--only-segment", first[0],
        ])
        self.assertEqual(code, 0)
        self.assertIn('"mode": "APPLY"', output)
        self.assertIn('"deleted": 1', output)
        self.assertTrue(output.find("RETENTION_DELETED") < output.find("NEGATIVE_RETENTION:"))
        self.assertFalse(first[1].exists())
        self.assertTrue(second[1].is_file())
        self.assertTrue(third[1].is_file())
        self.assertTrue(first[1].with_suffix(".json").is_file())
        self.assertEqual(len(retention.RECEIPTS.read_text(encoding="utf-8").splitlines()), 1)
        with closing(sqlite3.connect(self.db_path)) as db, db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone(), ("ok",))
            self.assertEqual(db.execute(
                "SELECT disposition FROM source_dispositions WHERE segment_id=?",
                (first[0],),
            ).fetchone(), ("NO_CANDIDATE_DELETED",))

    def test_apply_limit_is_hard_bounded(self):
        with self.assertRaises(SystemExit):
            retention.main(["--apply", "--max-deletes", "6"])

    def test_targeted_apply_cannot_exceed_one_file(self):
        segment, _ = self.add_job("cam-ruby", "20261104T184000Z")
        with self.assertRaises(SystemExit):
            retention.main(["--apply", "--max-deletes", "5", "--only-device", "cam-ruby",
                            "--only-segment", segment])

    def test_successful_result_without_time_fails_closed(self):
        segment, _ = self.add_job("cam-ruby", "20261104T184000Z")
        bad_segment = "legacy-no-utc-time"
        result_path = self.data / "detector-results" / "cam-ruby" / (bad_segment + "_result.json")
        result_bytes = json.dumps({"version": "test-v1", "results": []}).encode("utf-8")
        result_path.write_bytes(result_bytes)
        with closing(sqlite3.connect(self.db_path)) as db, db:
            cursor = db.execute(
                "INSERT INTO jobs(device_id,segment_id,video_sha256,finished_utc,state) "
                "VALUES(?,?,?,?,?)",
                ("cam-ruby", bad_segment, "0" * 64, "2026-11-04T19:00:00+00:00", "SUCCEEDED"),
            )
            db.execute(
                "INSERT INTO detector_runs(job_id,input_sha256,result_path,result_sha256) "
                "VALUES(?,?,?,?)",
                (cursor.lastrowid, "0" * 64, str(result_path), hashlib.sha256(result_bytes).hexdigest()),
            )
        db = retention.open_db(read_only=True)
        try:
            with self.assertRaisesRegex(RuntimeError, "SUCCESSFUL_ANALYSIS_TIME_UNPARSEABLE"):
                retention.candidate_times(db)
        finally:
            db.close()

    def test_wrapper_requires_explicit_policy_and_runs_backup_first(self):
        policy = self.state / "negative-video-retention-enabled.json"
        policy.write_text(json.dumps({
            "schema": "meteormesh.negative-retention-activation.v1",
            "activation_utc": "2026-11-04T18:32:12+00:00",
            "auto_delete_enabled": True,
        }), encoding="utf-8")
        with patch.object(wrapper, "RETENTION_POLICY", policy):
            self.assertTrue(wrapper.retention_enabled())
            policy.write_text(json.dumps({
                "schema": "meteormesh.negative-retention-activation.v1",
                "activation_utc": "2026-11-04T18:32:12+00:00",
                "auto_delete_enabled": False,
            }), encoding="utf-8")
            self.assertFalse(wrapper.retention_enabled())
        calls = []
        def fake_step(label, script, args, timeout):
            calls.append((label, list(args)))
            return "OK", ""
        with patch.object(wrapper, "run_step", side_effect=fake_step), \
                patch.object(wrapper, "retention_enabled", return_value=True):
            self.assertEqual(wrapper.main(), 0)
        self.assertEqual(calls[0][0], "DB_BACKUP_PRE_RETENTION")
        self.assertEqual(calls[1], ("NEGATIVE_RETENTION", ["--apply", "--max-deletes", "5"]))
        self.assertEqual(calls[2][0], "REPORT")

    def test_wrapper_skips_apply_when_backup_fails(self):
        calls = []
        def fake_step(label, script, args, timeout):
            calls.append(label)
            return ("ERROR_MISSING_BACKUP_RESULT", "") if label == "DB_BACKUP_PRE_RETENTION" else ("OK", "")
        with patch.object(wrapper, "run_step", side_effect=fake_step), \
                patch.object(wrapper, "retention_enabled", return_value=True):
            self.assertEqual(wrapper.main(), 0)
        self.assertEqual(calls, ["DB_BACKUP_PRE_RETENTION", "REPORT"])


if __name__ == "__main__":
    unittest.main()
