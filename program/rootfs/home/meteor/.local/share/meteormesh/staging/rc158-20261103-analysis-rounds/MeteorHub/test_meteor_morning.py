"""Simulation-only morning automation acceptance; no phones/network/MetDetPy required."""

import hashlib
import configparser
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import meteor_morning as morning
from meteormesh_hub import store_verified_segment


class MorningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "raw"
        self.cfg = self.root / "devices.json"
        self.cfg.write_text(json.dumps({"devices": [{
            "id": id_, "host": f"192.168.1.{50+i}", "port": 8443,
            "fingerprint": "a" * 64, "token_env": f"METEOR_TOKEN_{i}"
        } for i, id_ in enumerate(("cam-ruby", "cam-viva", "cam-surya"))]}))
        self.detector_cfg = self.root / "detector.json"
        self.detector_cfg.write_text('{"profile":"test"}')
        self.repo = self.root / "MetDetPy"
        self.repo.mkdir()
        self.args = {"config": self.cfg, "data_root": self.data,
                     "detector_repo": self.repo, "detector_config": self.detector_cfg,
                     "max_runtime_seconds": 3600, "max_jobs": 5, "clock": lambda: 0.0}

    def _verified(self, db, device, segment_id):
        data = b"FAKE_VIDEO_NOT_A_REAL_MP4"
        sha = hashlib.sha256(data).hexdigest()
        meta = {"segment_id": segment_id, "sha256": sha, "size_bytes": len(data)}
        store_verified_segment(db, device, meta, "INGEST_VERIFIED", {"mock": True})

    @staticmethod
    def _fake_detector(db, job, *_args, **_kwargs):
        db.execute("UPDATE jobs SET state='SUCCEEDED' WHERE id=?", (job["id"],))
        db.commit()
        return "SIMULATED_RESULT"

    def test_plan_is_offline_and_read_only(self):
        planned = morning.plan(self.cfg, self.data, self.repo, self.detector_cfg)
        self.assertFalse(planned["runs_now"])
        self.assertEqual(planned["device_count"], 3)
        self.assertFalse(self.data.exists())
        self.assertNotIn("token_value", json.dumps(planned))
        self.assertFalse(planned["auto_purge"])

    def test_plan_accepts_placeholder_certificates_but_live_run_does_not(self):
        raw = json.loads(self.cfg.read_text())
        raw["devices"][0]["fingerprint"] = "REPLACE_ON_PHONE"
        self.cfg.write_text(json.dumps(raw))
        planned = morning.plan(self.cfg, self.data, self.repo, self.detector_cfg)
        self.assertFalse(planned["config_ready_for_live_run"])
        self.assertEqual(planned["device_count"], 3)
        self.assertFalse(self.data.exists())
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            morning.run_morning(**self.args)

    def test_plan_rejects_wrong_json_shape_without_writing(self):
        self.cfg.write_text('[]')
        with self.assertRaisesRegex(ValueError, "devices array"):
            morning.plan(self.cfg, self.data, self.repo, self.detector_cfg)
        self.assertFalse(self.data.exists())

    def test_systemd_examples_are_unactivated_templates(self):
        service = Path(__file__).parent / "systemd/meteormesh-morning.service.example"
        timer = Path(__file__).parent / "systemd/meteormesh-morning.timer.example"
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(service)
        self.assertEqual(cfg["Service"]["Type"], "oneshot")
        self.assertIn("meteor_morning.py run", cfg["Service"]["ExecStart"])
        self.assertIn("EnvironmentFile", cfg["Service"])
        self.assertNotIn("ExecStartPost", cfg["Service"])
        cfg.read(timer)
        self.assertEqual(cfg["Timer"]["Persistent"], "true")
        self.assertIn("09:00:00", cfg["Timer"]["OnCalendar"])

    def test_run_single_worker_and_reports_no_deletion(self):
        def sync_stub(device, _folder, db, *_args, **_kwargs):
            self._verified(db, device["id"], device["id"].replace("-", "_") + "_001")
            return 1, 0

        empty_events = {"schema": "PROVISIONAL", "events": [], "provisional_groups": 0}
        with patch("meteor_morning.process_device", side_effect=sync_stub), \
             patch("meteor_morning.verify_metdet_repo", return_value="4825da0"), \
             patch("meteor_morning.run_one_detector", side_effect=self._fake_detector), \
             patch("meteor_morning.build_event_report", return_value=empty_events):
            output = morning.run_morning(**self.args)
        self.assertEqual(output["sync_good"], 3)
        self.assertEqual(output["jobs_enqueued"], 3)
        self.assertEqual(output["jobs_completed_this_run"], 3)
        self.assertEqual(output["outcome"], "COMPLETED_PROVISIONAL")
        self.assertFalse(output["session_analysis_closed"])
        self.assertFalse(output["phone_delete_enabled"])
        self.assertFalse(output["archive_verified"])
        self.assertEqual(output["health_inventory"]["scope"],
                         "ALL_SESSIONS_IN_LOCAL_DATABASE_NOT_A_CLOSED_NIGHT")
        self.assertFalse(output["health_inventory"]["retention_permission"])
        self.assertEqual(morning.last_run(self.data)["run_id"], output["run_id"])
        self.assertTrue(Path(output["provisional_event_report"]).is_file())

    def test_offline_phone_does_not_block_other_phones(self):
        def sync_stub(device, _folder, db, *_args, **_kwargs):
            if device["id"] == "cam-ruby":
                raise ConnectionError("simulated phone offline")
            return 0, 0

        with patch("meteor_morning.process_device", side_effect=sync_stub), \
             patch("meteor_morning.verify_metdet_repo", return_value="4825da0"):
            result = morning.run_morning(**self.args)
        self.assertEqual(len(result["devices"]), 3)
        self.assertEqual(result["devices"][0]["state"], "OFFLINE_OR_ERROR")
        self.assertEqual(result["devices"][1]["state"], "NO_READY_SEGMENTS")
        self.assertEqual(result["outcome"], "PARTIAL_REVIEW_REQUIRED")
        self.assertEqual(result["provisional_events_skipped"], "INGEST_OR_ANALYSIS_INCOMPLETE")

    def test_online_but_no_video_cannot_be_a_successful_night(self):
        with patch("meteor_morning.process_device", return_value=(0, 0)), \
             patch("meteor_morning.verify_metdet_repo", return_value="4825da0"), \
             patch("meteor_morning.build_event_report") as report_mock:
            result = morning.run_morning(**self.args)
        report_mock.assert_not_called()
        self.assertEqual(result["verified_segments_in_db"], 0)
        self.assertEqual(result["outcome"], "PARTIAL_REVIEW_REQUIRED")
        self.assertIn("No verified video segments", " ".join(result["errors"]))

    def test_old_inventory_cannot_hide_zero_ready_segments_today(self):
        self.data.mkdir()
        from meteormesh_pipeline import database_for
        with database_for(self.data, initialize=True) as db:
            self._verified(db, "cam-ruby", "old_night_video")
        with patch("meteor_morning.process_device", return_value=(0, 0)), \
             patch("meteor_morning.verify_metdet_repo", return_value="4825da0"), \
             patch("meteor_morning.build_event_report") as report_mock:
            result = morning.run_morning(**self.args)
        report_mock.assert_not_called()
        self.assertEqual(result["verified_segments_in_db"], 1)
        self.assertEqual(result["outcome"], "PARTIAL_REVIEW_REQUIRED")
        self.assertFalse(result["new_night_completeness_proven"])
        self.assertTrue(all(d["state"] == "NO_READY_SEGMENTS" for d in result["devices"]))

    def test_missing_detector_keeps_job_waiting_not_failed(self):
        def sync_stub(device, _folder, db, *_args, **_kwargs):
            if device["id"] == "cam-ruby":
                self._verified(db, device["id"], "ruby_one")
                return 1, 0
            return 0, 0

        with patch("meteor_morning.process_device", side_effect=sync_stub), \
             patch("meteor_morning.verify_metdet_repo", side_effect=RuntimeError("missing MetDetPy")):
            result = morning.run_morning(**self.args)
        self.assertEqual(result["outcome"], "PARTIAL_REVIEW_REQUIRED")
        self.assertEqual(result["queue"]["jobs"]["WAITING"], 1)
        self.assertEqual(result["jobs_failed_this_run"], 0)

    def test_resume_next_morning_without_duplicate_queue_entries(self):
        def sync_stub(device, _folder, db, *_args, **_kwargs):
            if device["id"] == "cam-ruby":
                self._verified(db, device["id"], "ruby_one")
                self._verified(db, device["id"], "ruby_two")
                return 2, 0
            return 0, 0

        empty_events = {"schema": "PROVISIONAL", "events": [], "provisional_groups": 0}
        with patch("meteor_morning.process_device", side_effect=sync_stub), \
             patch("meteor_morning.verify_metdet_repo", return_value="4825da0"), \
             patch("meteor_morning.run_one_detector", side_effect=self._fake_detector), \
             patch("meteor_morning.build_event_report", return_value=empty_events):
            first = morning.run_morning(**(self.args | {"max_jobs": 1}))
            second = morning.run_morning(**(self.args | {"max_jobs": 1}))
        self.assertEqual(first["jobs_enqueued"], 2)
        self.assertEqual(first["outcome"], "PARTIAL_REVIEW_REQUIRED")
        self.assertEqual(second["jobs_enqueued"], 0)
        self.assertEqual(second["jobs_completed_this_run"], 1)
        self.assertEqual(second["outcome"], "PARTIAL_REVIEW_REQUIRED")
        self.assertEqual(len(list((self.data / "morning-reports").glob("morning_*.json"))), 2)

    def test_analysis_batch_yields_successfully_when_queue_remains(self):
        from meteormesh_pipeline import database_for

        self.data.mkdir()
        with database_for(self.data, initialize=True) as db:
            self._verified(db, "cam-ruby", "ruby_20261102T200000Z_0123456789ab")
            self._verified(db, "cam-viva", "viva_20261102T200500Z_abcdef123456")
        empty_events = {"schema": "PROVISIONAL", "events": [], "provisional_groups": 0}
        with patch("meteor_morning.verify_metdet_repo", return_value="4825da0"), \
             patch("meteor_morning.run_one_detector", side_effect=self._fake_detector), \
             patch("meteor_morning.build_event_report", return_value=empty_events):
            result = morning.run_morning(**(self.args | {"phase": "analyze", "max_jobs": 1}))

        self.assertEqual(result["jobs_completed_this_run"], 1)
        self.assertEqual(result["queue"]["jobs"]["WAITING"], 1)
        self.assertTrue(result["analysis_batch_yielded"])
        self.assertEqual(result["outcome"], "ANALYSIS_BATCH_PENDING")

    def test_fair_sync_rounds_rotate_across_cameras(self):
        remaining = {"cam-ruby": 15, "cam-viva": 20, "cam-surya": 5}
        order = []

        def fair_sync(device, _folder, db, *_args, max_segments=None, skip_ids=None, **_kwargs):
            device_id = device["id"]
            order.append(device_id)
            take = min(max_segments, remaining[device_id])
            for index in range(take):
                segment_id = f"{device_id.replace('-', '_')}_20261102T200000Z_{(len(order) * 100 + index):012x}"
                skip_ids.add(segment_id)
                self._verified(db, device_id, segment_id)
            remaining[device_id] -= take
            return take, 0

        with patch.dict(os.environ, {"METEORMESH_FAIR_SYNC_ROUND_SIZE": "10"}), \
             patch("meteor_morning.process_device", side_effect=fair_sync):
            result = morning.run_morning(**(self.args | {"phase": "sync"}))

        self.assertEqual(order, ["cam-ruby", "cam-viva", "cam-surya", "cam-ruby", "cam-viva", "cam-viva"])
        self.assertEqual(result["sync_good"], 40)
        self.assertEqual(result["outcome"], "INGEST_QUEUED")

    def test_terminal_analysis_failure_is_review_required_not_pending(self):
        from meteormesh_pipeline import database_for

        self.data.mkdir()
        with database_for(self.data, initialize=True) as db:
            self._verified(db, "cam-ruby", "ruby_20261102T200000Z_0123456789ab")
        empty_events = {"schema": "PROVISIONAL", "events": [], "provisional_groups": 0}
        with patch("meteor_morning.verify_metdet_repo", return_value="4825da0"), \
             patch("meteor_morning.run_one_detector", side_effect=RuntimeError("simulated detector error")), \
             patch("meteor_morning.build_event_report", return_value=empty_events):
            result = morning.run_morning(**(self.args | {"phase": "analyze", "max_jobs": 1}))

        self.assertEqual(result["jobs_failed_this_run"], 1)
        self.assertEqual(result["queue"]["jobs"]["FAILED"], 1)
        self.assertFalse(result["analysis_batch_yielded"])
        self.assertEqual(result["outcome"], "ANALYSIS_COMPLETE_REVIEW_REQUIRED")

    def test_two_runs_cannot_share_same_lock(self):
        self.data.mkdir()
        with morning.exclusive_run_lock(self.data):
            with self.assertRaisesRegex(RuntimeError, "already active"):
                with morning.exclusive_run_lock(self.data):
                    pass

    def test_no_ffprobe_does_not_create_false_verified_data(self):
        with patch("meteor_morning.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "ffprobe required"):
                morning.run_morning(**self.args)
        self.assertFalse(self.data.exists())

    def test_runtime_budget_stops_work_and_returns_review_status(self):
        class RapidClock:
            calls = 0

            def __call__(self):
                self.calls += 1
                return 0.0 if self.calls == 1 else 4000.0

        with patch("meteor_morning.process_device") as sync_mock:
            result = morning.run_morning(**(self.args | {"clock": RapidClock()}))
        sync_mock.assert_not_called()
        self.assertEqual(result["outcome"], "PARTIAL_REVIEW_REQUIRED")
        self.assertEqual([row["state"] for row in result["devices"]],
                         ["SKIPPED_TIME_BUDGET"] * 3)
        self.assertEqual(result["sync_good"], 0)

    def test_report_target_cannot_replace_existing_file(self):
        self.data.mkdir()
        reports = morning.reports_folder(self.data)
        path = reports / "fixed.json"
        path.write_text("important original")
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            morning.write_new_report(reports, "fixed.json", {"status": "something"})
        self.assertEqual(path.read_text(), "important original")

if __name__ == "__main__":
    unittest.main()
