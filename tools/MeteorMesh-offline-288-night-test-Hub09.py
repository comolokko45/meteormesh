#!/usr/bin/env python3
"""Offline MeteorHub morning-run stress/regression simulation.

Run on HP G62 over SSH with:
    python3 MeteorMesh-offline-288-night-test-Hub09.py -v

Optionally override the Hub source path for isolated verification:
    METEORMESH_HUB_SOURCE=/path/to/MeteorHub-0.9 python3 MeteorMesh-offline-288-night-test-Hub09.py -v

All 288 segments, phone responses, detector results, and event reports are
SIMULATED. No video is produced or decoded. No phone, network, cloud, actual
MetDetPy, or existing MeteorMesh data directory is accessed. This proves only
the SQLite queue/orchestration and fail-closed report behavior at this scale.
Temporary test data is confined to a newly created TemporaryDirectory.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HUB_SOURCE = Path(os.environ.get("METEORMESH_HUB_SOURCE",
                                 "/opt/meteormesh/src/MeteorHub-0.9")).resolve()
if not HUB_SOURCE.is_dir() or not (HUB_SOURCE / "meteor_morning.py").is_file():
    raise RuntimeError(f"MeteorHub-0.9 source directory required: {HUB_SOURCE}")
sys.path.insert(0, str(HUB_SOURCE))

import meteor_morning as morning  # noqa: E402
from hub_workflow import retry_failed  # noqa: E402
from meteormesh_hub import store_verified_segment  # noqa: E402
from meteormesh_pipeline import database_for  # noqa: E402


DEVICES = ("cam-ruby", "cam-viva", "cam-surya")
SEGMENTS_PER_CAMERA = 96  # 8 hours at nominal five-minute intervals
SIMULATED_EVENT_REPORT = {"schema": "PROVISIONAL_SIMULATION_ONLY",
                          "events": [], "provisional_groups": 0}


class OfflineNightSimulation(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="meteormesh-offline-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data_root = self.root / "meteor-data"
        self.config = self.root / "devices.json"
        self.config.write_text(json.dumps({"devices": [
            {"id": device, "host": f"192.168.1.{50+i}", "port": 8443,
             "fingerprint": "b" * 64, "token_env": f"SIM_TOKEN_{i}"}
            for i, device in enumerate(DEVICES)
        ]}), encoding="utf-8")
        self.detector_config = self.root / "detector.json"
        self.detector_config.write_text('{"simulation_only":true}', encoding="utf-8")
        self.detector_repo = self.root / "metdetpy-not-used"
        self.detector_repo.mkdir()

    @staticmethod
    def simulate_ingest(db: sqlite3.Connection, device: str, count: int) -> None:
        """Inject fake ingest rows only into a temporary test database."""
        for index in range(count):
            name = f"{device.removeprefix('cam-')}_sim_{index:03d}"
            digest = hashlib.sha256(f"SIMULATED:{device}/{name}".encode()).hexdigest()
            store_verified_segment(db, device, {
                "segment_id": name,
                "sha256": digest,
                "size_bytes": 1024,
            }, "INGEST_VERIFIED", {"SIMULATION_ONLY": True})

    @staticmethod
    def simulate_detector(db: sqlite3.Connection, job: dict, *_args, **_kwargs) -> str:
        db.execute("UPDATE jobs SET state='SUCCEEDED' WHERE id=?", (job["id"],))
        db.commit()
        return "SIMULATED_DETECTOR_NO_VIDEO_PROCESSED"

    def run_simulated(self, sync_stub, detector_stub=None) -> dict:
        """Run the real coordinator with clearly mocked device/AI/media I/O."""
        with patch("meteor_morning.shutil.which", return_value="SIMULATED_FFPROBE"), \
             patch("meteor_morning.process_device", side_effect=sync_stub), \
             patch("meteor_morning.verify_metdet_repo", return_value="SIMULATED_COMMIT"), \
             patch("meteor_morning.run_one_detector",
                   side_effect=detector_stub or self.simulate_detector), \
             patch("meteor_morning.build_event_report",
                   return_value=SIMULATED_EVENT_REPORT):
            return morning.run_morning(
                config=self.config,
                data_root=self.data_root,
                detector_repo=self.detector_repo,
                detector_config=self.detector_config,
                max_jobs=288,
                max_runtime_seconds=3600,
            )

    def assert_safe_and_unproven(self, report: dict) -> None:
        self.assertFalse(report["new_night_completeness_proven"])
        self.assertFalse(report["archive_verified"])
        self.assertFalse(report["session_analysis_closed"])
        self.assertFalse(report["retention_enabled"])
        self.assertFalse(report["phone_delete_enabled"])
        self.assertFalse(report["hub_delete_enabled"])
        self.assertFalse(report["llm_required"])
        self.assertFalse(report["health_inventory"]["retention_permission"])

    def test_288_simulated_segments_and_idempotent_retry(self) -> None:
        def all_online(device, _folder, db, *_args, **_kwargs):
            self.simulate_ingest(db, device["id"], SEGMENTS_PER_CAMERA)
            return SEGMENTS_PER_CAMERA, 0

        first = self.run_simulated(all_online)
        self.assertEqual(first["sync_good"], 288)
        self.assertEqual(first["jobs_enqueued"], 288)
        self.assertEqual(first["jobs_completed_this_run"], 288)
        self.assertEqual(first["queue"]["jobs"].get("SUCCEEDED"), 288)
        self.assertEqual(first["outcome"], "COMPLETED_PROVISIONAL")
        self.assert_safe_and_unproven(first)

        second = self.run_simulated(all_online)
        self.assertEqual(second["jobs_enqueued"], 0)
        self.assertEqual(second["jobs_completed_this_run"], 0)
        self.assertEqual(second["queue"]["jobs"].get("SUCCEEDED"), 288)
        self.assertEqual(second["outcome"], "COMPLETED_PROVISIONAL")
        self.assert_safe_and_unproven(second)

        with database_for(self.data_root) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM segments").fetchone()[0], 288)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 288)
        self.assertEqual(len(list((self.data_root / "morning-reports")
                                  .glob("morning_*.json"))), 2)
        print("SIM_288_OK: 288 kuyruk isi, 288 SUCCEEDED; ikinci calistirmada 0 yeni is")

    def test_missing_phone_and_partial_capture_resume_without_loss(self) -> None:
        def interrupted(device, _folder, db, *_args, **_kwargs):
            if device["id"] == "cam-surya":
                raise ConnectionError("SIMULATED_OFFLINE")
            count = 96 if device["id"] == "cam-ruby" else 64
            self.simulate_ingest(db, device["id"], count)
            return count, (0 if count == 96 else 32)

        partial = self.run_simulated(interrupted)
        self.assertEqual(partial["sync_good"], 160)
        self.assertEqual(partial["jobs_enqueued"], 160)
        self.assertEqual(partial["outcome"], "PARTIAL_REVIEW_REQUIRED")
        self.assertEqual(partial["devices"][1]["state"], "INCOMPLETE")
        self.assertEqual(partial["devices"][2]["state"], "OFFLINE_OR_ERROR")
        self.assertEqual(partial["provisional_events_skipped"],
                         "INGEST_OR_ANALYSIS_INCOMPLETE")
        self.assert_safe_and_unproven(partial)

        def restored(device, _folder, db, *_args, **_kwargs):
            self.simulate_ingest(db, device["id"], SEGMENTS_PER_CAMERA)
            return SEGMENTS_PER_CAMERA, 0

        resumed = self.run_simulated(restored)
        self.assertEqual(resumed["jobs_enqueued"], 128)
        self.assertEqual(resumed["queue"]["jobs"].get("SUCCEEDED"), 288)
        self.assertEqual(resumed["outcome"], "COMPLETED_PROVISIONAL")
        self.assert_safe_and_unproven(resumed)
        print("SIM_OFFLINE_RECOVERY_OK: 160 -> 288; 128 yeni is; kaynak video yok")

    def test_detector_failure_needs_manual_retry_then_recovers(self) -> None:
        """FAILED is fail-closed; no implicit retry until retry_failed is called."""
        def three_online(device, _folder, db, *_args, **_kwargs):
            self.simulate_ingest(db, device["id"], 1)
            return 1, 0

        def failing_detector(db, job, *_args, **_kwargs):
            if job["device_id"] == "cam-ruby":
                raise RuntimeError("SIMULATED_DETECTOR_FAILURE")
            return self.simulate_detector(db, job)

        first = self.run_simulated(three_online, failing_detector)
        self.assertEqual(first["jobs_failed_this_run"], 1)
        self.assertEqual(first["queue"]["jobs"].get("FAILED"), 1)
        self.assertEqual(first["queue"]["jobs"].get("SUCCEEDED"), 2)
        self.assertEqual(first["outcome"], "PARTIAL_REVIEW_REQUIRED")
        self.assert_safe_and_unproven(first)

        repeated = self.run_simulated(three_online)
        self.assertEqual(repeated["jobs_enqueued"], 0)
        self.assertEqual(repeated["jobs_completed_this_run"], 0)
        self.assertEqual(repeated["queue"]["jobs"].get("FAILED"), 1)
        self.assertEqual(repeated["outcome"], "PARTIAL_REVIEW_REQUIRED")
        self.assert_safe_and_unproven(repeated)

        with database_for(self.data_root) as db:
            self.assertEqual(retry_failed(db), 1)

        restored = self.run_simulated(three_online)
        self.assertEqual(restored["jobs_enqueued"], 0)
        self.assertEqual(restored["jobs_completed_this_run"], 1)
        self.assertEqual(restored["queue"]["jobs"].get("SUCCEEDED"), 3)
        self.assertEqual(restored["outcome"], "COMPLETED_PROVISIONAL")
        self.assert_safe_and_unproven(restored)
        print("SIM_DETECTOR_MANUAL_RETRY_OK: FAILED durur; manuel retry sonrasi SUCCEEDED")

    def test_same_segment_id_with_changed_content_is_rejected(self) -> None:
        with database_for(self.data_root, initialize=True) as db:
            self.simulate_ingest(db, "cam-ruby", 1)
            with self.assertRaisesRegex(RuntimeError, "identity changed"):
                store_verified_segment(db, "cam-ruby", {
                    "segment_id": "ruby_sim_000",
                    "sha256": "a" * 64,
                    "size_bytes": 1024,
                }, "INGEST_VERIFIED", {"SIMULATION_ONLY": True})
            row = db.execute(
                "SELECT sha256 FROM segments WHERE device_id='cam-ruby'"
            ).fetchone()
            self.assertNotEqual(row[0], "a" * 64)


if __name__ == "__main__":
    unittest.main()
