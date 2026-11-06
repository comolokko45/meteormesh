"""Phone-free tests for conservative health, manual review and opt-in lab tools."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meteor_health import summarize_health
from meteor_lab import (checked_video, detect_sample, detector_command,
                        mask_info, planned_detection, sampled_frame)
from meteor_review import append_review, read_reviews, source_from_report
from meteormesh_hub import create_database, store_verified_segment
from hub_workflow import init_pipeline


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = create_database(self.root / "meteorhub.sqlite3")
        self.addCleanup(self.db.close)
        init_pipeline(self.db)

    def _segment(self, device, segment, state, duration=None):
        source = b"source"
        digest = hashlib.sha256(source).hexdigest()
        probe = ({"format": {"duration": str(duration)}} if duration is not None else None)
        store_verified_segment(self.db, device, {
            "segment_id": segment, "sha256": digest, "size_bytes": len(source)
        }, state, probe)

    def test_empty_is_unverified_not_success(self):
        report = summarize_health(self.db, ["cam-ruby", "cam-viva"])
        self.assertFalse(report["capture_session_complete"])
        self.assertFalse(report["retention_permission"])
        self.assertEqual(report["device_health"]["cam-ruby"]["frame_pts_gap_count"], "UNVERIFIED")

    def test_mixed_probes_are_not_silent_gap_zero(self):
        self._segment("cam-ruby", "ruby_a", "INGEST_VERIFIED", 300)
        self._segment("cam-ruby", "ruby_b", "INGEST_VERIFIED")
        self._segment("cam-ruby", "ruby_bad", "FFPROBE_FAILED")
        self._segment("cam-viva", "viva_a", "INGEST_VERIFIED", 302)
        doc = summarize_health(self.db, ["cam-ruby", "cam-viva"])
        ruby = doc["device_health"]["cam-ruby"]
        self.assertEqual(ruby["segments_in_inventory"], 3)
        self.assertEqual(ruby["segments_ingest_verified"], 2)
        self.assertEqual(ruby["verified_video_duration_seconds"], 300)
        self.assertEqual(ruby["verified_segments_missing_duration"], 1)
        self.assertEqual(ruby["nonverified_segments"], 1)
        self.assertIn("ALL_SESSIONS", doc["scope"])

    def test_bad_probe_is_unknown_not_fake_duration(self):
        self._segment("cam-ruby", "ruby_a", "INGEST_VERIFIED", "nan")
        doc = summarize_health(self.db, ["cam-ruby"])
        self.assertEqual(doc["device_health"]["cam-ruby"]["verified_segments_missing_duration"], 1)
        self.assertIsNone(doc["device_health"]["cam-ruby"]["detector_processing_ratio_R"])

    def test_latest_detector_ratio_when_provenance_exists(self):
        self._segment("cam-ruby", "ruby_a", "INGEST_VERIFIED", 100)
        for i, elapsed in enumerate((90.0, 30.0)):
            job = self.db.execute("""INSERT INTO jobs
                (unique_key, kind, device_id, segment_id, video_sha256, detector_version,
                 detector_config_sha256, state, created_utc) VALUES (?, 'DETECT', 'cam-ruby',
                 'ruby_a', ?, 'v2.6.1', ?, 'SUCCEEDED', 'x')""",
                 (f"key{i}", "a" * 64, "b" * 64)).lastrowid
            self.db.execute("""INSERT INTO detector_runs
                (job_id, device_id, segment_id, input_sha256, detector_version,
                 detector_commit, config_sha256, result_path, result_sha256,
                 duration_seconds, completed_utc)
                 VALUES (?, 'cam-ruby', 'ruby_a', ?, 'v2.6.1', 'c', ?, 'p', ?, ?, 'now')""",
                 (job, "a" * 64, "b" * 64, "d" * 64, elapsed))
        self.db.commit()
        doc = summarize_health(self.db, ["cam-ruby"])
        self.assertEqual(doc["device_health"]["cam-ruby"]["detector_latest_run_count"], 1)
        self.assertEqual(doc["device_health"]["cam-ruby"]["detector_processing_ratio_R"], 0.3)

    def test_duplicate_camera_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            summarize_health(self.db, ["cam-ruby", "cam-ruby"])

    def test_health_cli_accepts_relative_data_directory(self):
        tool = Path(__file__).with_name("meteor_health.py")
        result = subprocess.run([sys.executable, str(tool), "--data-dir", self.root.name],
                                cwd=self.root.parent, capture_output=True, text=True, check=True)
        doc = json.loads(result.stdout)
        self.assertFalse(doc["retention_permission"])
        self.assertIn("cam-ruby", doc["device_health"])


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.report = self.root / "events.json"
        self.source_hash = "a" * 64
        self.candidate = "b" * 20
        self.report.write_text(json.dumps({
            "schema": "meteormesh.event.provisional.v0.2", "auto_delete": False, "events": [
                {"candidates": [{"candidate_id": self.candidate, "device_id": "cam-ruby",
                                 "segment_id": "seg01", "source_sha256": self.source_hash}]}]
        }))

    def test_annotation_append_and_revision_without_deletion(self):
        one = append_review(self.root, self.report, self.candidate, "meteor_possible")
        two = append_review(self.root, self.report, self.candidate, "uncertain", "daha net degil")
        records = read_reviews(self.root, self.candidate)
        self.assertEqual(len(records), 2)
        self.assertEqual([r["label"] for r in records], ["meteor_possible", "uncertain"])
        self.assertEqual(two["event_report_sha256"], one["event_report_sha256"])
        self.assertTrue(all(r["retention_permission"] is False for r in records))
        self.assertFalse((self.root / "meteorhub.sqlite3").exists())

    def test_cannot_label_unknown_candidate(self):
        with self.assertRaisesRegex(ValueError, "missing or duplicated"):
            append_review(self.root, self.report, "c" * 20, "meteor_possible")
        self.assertFalse((self.root / "candidate-review.jsonl").exists())

    def test_cannot_label_untrusted_report_type(self):
        data = json.loads(self.report.read_text())
        data["auto_delete"] = True
        self.report.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "Not a MeteorMesh"):
            append_review(self.root, self.report, self.candidate, "meteor_possible")

    def test_rejects_unsafe_label_or_note(self):
        with self.assertRaises(ValueError):
            append_review(self.root, self.report, self.candidate, "CONFIRMED_PURGE")
        with self.assertRaises(ValueError):
            append_review(self.root, self.report, self.candidate, "artifact", "x" * 281)

    def test_rejects_symlink_report_and_log(self):
        alias = self.root / "alias.json"
        alias.symlink_to(self.report)
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            source_from_report(alias, self.candidate)
        destination = self.root / "other.txt"
        destination.write_text("do not overwrite")
        (self.root / "candidate-review.jsonl").symlink_to(destination)
        with self.assertRaises(ValueError):
            append_review(self.root, self.report, self.candidate, "artifact")
        self.assertEqual(destination.read_text(), "do not overwrite")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "ffmpeg and ffprobe needed for media experiment tests")
class LabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.video = self.root / "sample.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "color=c=black:s=320x240:r=10:d=2", "-c:v", "mpeg4",
                        "-y", str(self.video)], check=True, capture_output=True)
        self.config = self.root / "config.json"
        self.config.write_text('{"test":true}')
        self.repo = self.root / "MetDetPy"
        self.repo.mkdir()
        self.mask = self.root / "mask.png"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "color=c=black:s=320x240:d=0.2", "-frames:v", "1",
                        "-update", "1", "-y", str(self.mask)],
                       check=True, capture_output=True)

    def test_plan_probes_video_and_mask_without_writing(self):
        existing = set(self.root.iterdir())
        doc = planned_detection(self.video, self.config, self.mask, "high", "720")
        self.assertEqual(doc["video"]["width"], 320)
        self.assertIsNotNone(doc["mask"]["sha256"])
        self.assertTrue(doc["mask"]["mask_decoded"])
        self.assertFalse(doc["retention_permission"])
        self.assertEqual(existing, set(self.root.iterdir()))

    def test_mask_mismatch_fails_closed(self):
        invalid = self.root / "wrong.png"
        invalid.write_bytes(self.mask.read_bytes()[:16] + b"\x00\x00\x00d\x00\x00\x00\xc8")
        with self.assertRaisesRegex(ValueError, "dimensions"):
            planned_detection(self.video, self.config, invalid)

    def test_fake_png_header_with_no_pixels_is_rejected(self):
        invalid = self.root / "truncated.png"
        invalid.write_bytes(self.mask.read_bytes()[:24])
        with self.assertRaisesRegex(ValueError, "cannot be decoded"):
            planned_detection(self.video, self.config, invalid)

    def test_detector_command_has_no_shell_or_delete(self):
        argv = detector_command(self.repo, self.video, self.config, self.root / "out.json",
                                self.mask, "low", "540")
        self.assertEqual(argv[argv.index("--mask") + 1], str(self.mask))
        self.assertEqual(argv[argv.index("--sensitivity") + 1], "low")
        self.assertNotIn("rm", argv)

    def test_lab_mdrf_result_is_separate_and_no_clobber(self):
        result = self.root / "new-detection.json"

        def fake_run(argv, **_kwargs):
            Path(argv[argv.index("--save-path") + 1]).write_text('{"results":[]}')
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        original = hashlib.sha256(self.video.read_bytes()).hexdigest()
        with patch("meteor_lab.verify_metdet_repo", return_value="4825da0"):
            audit = detect_sample(repo=self.repo, video=self.video, config=self.config,
                                  output=result, mask=self.mask, run_command=fake_run)
        self.assertEqual(audit["scientific_recall"], "UNVERIFIED_WITHOUT_HUMAN_LABELS")
        self.assertTrue((self.root / "new-detection.json.audit.json").is_file())
        self.assertEqual(hashlib.sha256(self.video.read_bytes()).hexdigest(), original)
        with self.assertRaises(FileExistsError):
            detect_sample(repo=self.repo, video=self.video, config=self.config,
                          output=result, run_command=fake_run)

    def test_detector_cannot_publish_if_source_mutates_midrun(self):
        result = self.root / "changed.json"
        def fake_run(argv, **_kwargs):
            Path(argv[argv.index("--save-path") + 1]).write_text('{"results":[]}')
            # Same-length tampering would pass a naive filesize check.
            source = self.video.read_bytes()
            self.video.write_bytes(source[:-1] + bytes([source[-1] ^ 1]))
            return subprocess.CompletedProcess(argv, 0, "", "")
        with patch("meteor_lab.verify_metdet_repo", return_value="4825da0"):
            with self.assertRaisesRegex(RuntimeError, "input changed"):
                detect_sample(repo=self.repo, video=self.video, config=self.config,
                              output=result, run_command=fake_run)
        self.assertFalse(result.exists())
        self.assertFalse((self.root / "changed.json.audit.json").exists())

    def test_sample_frame_generates_jpeg_without_source_change(self):
        dest = self.root / "one.jpg"
        original = hashlib.sha256(self.video.read_bytes()).hexdigest()
        result = sampled_frame(self.video, dest, 0.4)
        self.assertEqual(result["scientific_quality_classification"], "NOT_PERFORMED")
        self.assertGreater(dest.stat().st_size, 100)
        self.assertEqual(hashlib.sha256(self.video.read_bytes()).hexdigest(), original)
        with self.assertRaises(FileExistsError):
            sampled_frame(self.video, dest, 0.5)

    def test_sample_time_cannot_exceed_video(self):
        with self.assertRaisesRegex(ValueError, "timestamp"):
            sampled_frame(self.video, self.root / "bad.jpg", 20)


if __name__ == "__main__":
    unittest.main()
