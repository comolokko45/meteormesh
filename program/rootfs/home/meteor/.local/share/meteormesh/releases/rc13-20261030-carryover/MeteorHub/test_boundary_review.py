"""MeteorHub 0.3 offline boundary and source-index tests."""

import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import boundary_review
import event_package
import meteor_agent
from hub_events import build_event_report
from hub_workflow import claim_next, enqueue_verified
from meteormesh_hub import store_verified_segment
from test_hub_pipeline import BasePipelineTest


class PlanTests(BasePipelineTest):
    def test_no_pair_across_long_gap(self):
        self.make_segment("cam-ruby", "a001", started=1_000_000, seconds=300)
        self.make_segment("cam-ruby", "b001", started=1_400_000, seconds=300)
        self.make_segment("cam-ruby", "c001", started=1_700_000, seconds=300)
        p = boundary_review.plan_boundaries(self.db, self.root)
        self.assertEqual(len(p["pairs"]), 2)
        self.assertEqual(p["eligible_pairs"], 1)
        self.assertEqual(p["pairs"][0]["decision"], "NOT_STITCHED_UNCERTAIN_OR_LONG_CLOCK_GAP")
        self.assertFalse(p["pairs"][1]["seam_continuity_proven"])

    def test_no_pair_with_one_source(self):
        self.make_segment("cam-ruby", "a001")
        self.assertEqual(boundary_review.plan_boundaries(self.db, self.root)["eligible_pairs"], 0)

    def test_changed_sidecar_is_fatal(self):
        self.make_segment("cam-ruby", "a001")
        (self.root / "cam-ruby" / "a001.json").write_text('{"segment_id":"evil"}')
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            boundary_review.plan_boundaries(self.db, self.root)

    def test_agent_briefing_has_no_llm_or_purge(self):
        self.make_segment("cam-ruby", "a001")
        enqueue_verified(self.db, self.cfg_sha)
        with meteor_agent.read_only_connection(self.root / "meteorhub.sqlite3") as ro:
            data = meteor_agent.answer(ro, "briefing")
        self.assertEqual(data["dil"], "tr")
        self.assertEqual(data["bekleyen_analiz"], 1)
        self.assertFalse(data["otomatik_silme"])
        self.assertFalse(data["yapay_zeka_modeli_aktif"])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "ffmpeg/ffprobe needed for offline video smoke test")
class RealClipTests(BasePipelineTest):
    def add_real_video(self, segment: str, start: int, color: str):
        device = "cam-ruby"
        folder = self.root / device
        folder.mkdir(exist_ok=True)
        target = folder / f"{segment}.mp4"
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi",
               "-i", f"color=c={color}:s=320x180:r=10:d=3", "-an",
               "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(target)]
        subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        sidecar = {"segment_id": segment, "sha256": sha, "size_bytes": target.stat().st_size,
                   "start_utc_ms_estimate": start, "end_utc_ms_estimate": start + 3000,
                   "nominal_fps": 10}
        (folder / f"{segment}.json").write_text(json.dumps(sidecar))
        store_verified_segment(self.db, device, sidecar, "INGEST_VERIFIED", {"test": True})
        return target, sha

    def create_review(self):
        left, left_sha = self.add_real_video("left001", 1_000_000, "black")
        right, right_sha = self.add_real_video("right001", 1_003_000, "white")
        plan = boundary_review.plan_boundaries(self.db, self.root, seconds=2)
        self.assertEqual(plan["eligible_pairs"], 1)
        output_dir = self.root / "review"
        metadata = boundary_review.build_review(plan["pairs"][0], output_dir, 2)
        self.assertEqual(hashlib.sha256(left.read_bytes()).hexdigest(), left_sha)
        self.assertEqual(hashlib.sha256(right.read_bytes()).hexdigest(), right_sha)
        return output_dir, metadata

    def test_actual_ffmpeg_short_review_and_repeat(self):
        output, meta = self.create_review()
        clip = output / meta["derived_video"]
        self.assertTrue(clip.is_file())
        self.assertEqual(meta["source_segments"][0]["segment_id"], "left001")
        self.assertAlmostEqual(meta["derived_duration_seconds"], 4, delta=0.25)
        self.assertFalse(meta["retention_permission"])
        self.assertFalse(meta["seam_continuity_proven"])
        pair = boundary_review.plan_boundaries(self.db, self.root)["pairs"][0]
        self.assertEqual(boundary_review.build_review(pair, output, 2)["derived_sha256"],
                         meta["derived_sha256"])
        self.assertTrue(clip.exists())

    def test_boundary_detector_adapter_and_source_index(self):
        output, meta = self.create_review()
        manifest = output / f"{meta['pair_id']}.manifest.json"
        synthetic_mdrf = self.mdrf([10])

        def mock_run(args, **kwargs):
            self.assertEqual(args[-2], "--save-path")
            Path(args[-1]).write_text(json.dumps(synthetic_mdrf))
            return subprocess.CompletedProcess(args, 0, "", "")

        with patch("boundary_review.verify_metdet_repo", return_value="4825da0abcdef"):
            audit = boundary_review.run_boundary_detector(manifest, self.fake_repo,
                                                          self.config, command=mock_run)
        self.assertEqual(audit["candidate_count_unreviewed"], 1)
        self.assertFalse(audit["retention_permission"])
        # Empty normal-event report plus a boundary-only candidate must still be retained.
        report_file = self.root / "normal-events.json"
        report_file.write_text(json.dumps({"schema": "meteormesh.event.provisional.v0.2",
                                           "auto_delete": False, "events": []}))
        verifier = event_package.SourceVerifier(self.db, self.root)
        indexes = event_package.build_indexes(verifier, report_file, output)
        self.assertEqual(len(indexes), 1)
        self.assertEqual(indexes[0]["candidate_count"], 1)
        self.assertEqual(len(indexes[0]["source_videos"]), 2)
        self.assertFalse(indexes[0]["drive_verified"])
        self.assertFalse(indexes[0]["retention_permission"])
        saved = event_package.save_indexes(indexes, self.root / "indexes")
        self.assertEqual(len(saved), 1)
        with self.assertRaises(FileExistsError):
            event_package.save_indexes(indexes, self.root / "indexes")


class ManifestTests(BasePipelineTest):
    def test_normal_candidate_source_index_keeps_originals(self):
        video, sidecar = self.make_segment("cam-ruby", "r001")
        self.make_segment("cam-viva", "v001")
        first = video.read_bytes()
        enqueue_verified(self.db, self.cfg_sha)
        job = claim_next(self.db)
        self.complete_with_mock_detector(job, [25])
        report = build_event_report(self.db, self.root, ["cam-ruby", "cam-viva", "cam-surya"])
        file = self.root / "events.json"
        file.write_text(json.dumps(report))
        indexes = event_package.build_indexes(event_package.SourceVerifier(self.db, self.root), file)
        self.assertEqual(len(indexes), 1)
        self.assertEqual(len(indexes[0]["source_videos"]), 2)
        self.assertEqual(video.read_bytes(), first)
        self.assertFalse(indexes[0]["archived_originals"])
        self.assertFalse(indexes[0]["originals_copied_to_package"])
        self.assertFalse(indexes[0]["retention_permission"])

    def test_index_rejects_original_mutation(self):
        video, sidecar = self.make_segment("cam-ruby", "r001")
        with self.assertRaisesRegex(RuntimeError, "Video hash changed"):
            video.write_bytes(b"different bytes but same size"[:video.stat().st_size])
            event_package.SourceVerifier(self.db, self.root).check("cam-ruby", "r001", sidecar["sha256"])

    def test_malicious_source_id_rejected(self):
        self.make_segment("cam-ruby", "r001")
        with self.assertRaises(ValueError):
            event_package.SourceVerifier(self.db, self.root).check("cam-ruby", "../etc", "a" * 64)


if __name__ == "__main__":
    unittest.main()
