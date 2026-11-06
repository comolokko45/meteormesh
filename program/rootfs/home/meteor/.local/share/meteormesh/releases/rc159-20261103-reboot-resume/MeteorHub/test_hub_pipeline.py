"""Offline safety tests; no phones, MetDetPy install, network or raw video needed."""

import contextlib
import hashlib
import io
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import meteor_agent
import meteormesh_pipeline
from hub_events import (Candidate, build_event_report, candidates_from_mdrf,
                        context_for_event, group_provisional, parse_relative_time)
from hub_workflow import (claim_next, enqueue_verified, init_pipeline, mark_failed,
                          queue_summary, reclaim_stale, retry_failed, run_one_detector)
from meteormesh_hub import create_database, store_verified_segment


class BasePipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = create_database(self.root / "meteorhub.sqlite3")
        self.addCleanup(self.db.close)
        init_pipeline(self.db)
        self.config = self.root / "meteor_config.yaml"
        self.config.write_text("detector: sample_test_config\n", encoding="utf-8")
        self.cfg_sha = hashlib.sha256(self.config.read_bytes()).hexdigest()
        self.fake_repo = self.root / "MetDetPy"
        self.fake_repo.mkdir()

    def make_segment(self, device="ruby", segment="s0001", started=1_000_000,
                     seconds=300):
        folder = self.root / device
        folder.mkdir(exist_ok=True)
        video = folder / f"{segment}.mp4"
        video.write_bytes(f"ORIGINAL_{device}_{segment}".encode())
        digest = hashlib.sha256(video.read_bytes()).hexdigest()
        sidecar = {"segment_id": segment, "sha256": digest,
                   "size_bytes": video.stat().st_size,
                   "start_utc_ms_estimate": started,
                   "end_utc_ms_estimate": started + seconds * 1000,
                   "nominal_fps": 25}
        (folder / f"{segment}.json").write_text(json.dumps(sidecar), encoding="utf-8")
        store_verified_segment(self.db, device, sidecar, "INGEST_VERIFIED", {"mock": True})
        return video, sidecar

    @staticmethod
    def mdrf(frames=None):
        return {"version": "0.5", "basic_info": {"fps": 25},
                "results": [{"target": [{"start_frame": frame, "score": 0.9,
                                          "category": "METEOR"} for frame in (frames or [])]}]}

    def complete_with_mock_detector(self, job, frames):
        fake_doc = self.mdrf(frames)

        def stub(argv, **kwargs):
            self.assertNotIn("shell", kwargs)
            self.assertEqual(argv[0], __import__("sys").executable)
            self.assertEqual(argv[-2], "--save-path")
            Path(argv[-1]).write_text(json.dumps(fake_doc), encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch("hub_workflow.verify_metdet_repo", return_value="4825da0aabbcc"), \
             patch("meteormesh_hub.verify_with_ffprobe", return_value=("INGEST_VERIFIED", {})):
            return run_one_detector(self.db, job, self.root, self.fake_repo, self.config,
                                    run_command=stub)


class QueueTests(BasePipelineTest):
    def test_idempotent_queue_only_verified(self):
        self.make_segment()
        other, meta = self.make_segment("viva", "s0002")
        store_verified_segment(self.db, "viva", meta, "HASH_VERIFIED_FFPROBE_MISSING", None)
        self.assertEqual(enqueue_verified(self.db, self.cfg_sha), 1)
        self.assertEqual(enqueue_verified(self.db, self.cfg_sha), 0)
        self.assertEqual(queue_summary(self.db)["jobs"], {"WAITING": 1})

    def test_atomic_claim_and_failure_retry(self):
        self.make_segment()
        enqueue_verified(self.db, self.cfg_sha)
        job = claim_next(self.db, now=100.0)
        self.assertIsNotNone(job)
        self.assertIsNone(claim_next(self.db))
        mark_failed(self.db, job["id"], "Detector unavailable")
        self.assertEqual(queue_summary(self.db)["jobs"], {"FAILED": 1})
        self.assertEqual(retry_failed(self.db), 1)
        self.assertEqual(claim_next(self.db)["id"], job["id"])

    def test_reclaim_after_worker_crash(self):
        self.make_segment()
        enqueue_verified(self.db, self.cfg_sha)
        job = claim_next(self.db, now=100.0)
        self.assertEqual(reclaim_stale(self.db, lease_seconds=600, now=699), 0)
        self.assertEqual(reclaim_stale(self.db, lease_seconds=600, now=701), 1)
        self.assertEqual(claim_next(self.db, now=702)["id"], job["id"])

    def test_detector_preserves_input_and_publishes_verified_result(self):
        video, sidecar = self.make_segment()
        original = video.read_bytes()
        enqueue_verified(self.db, self.cfg_sha)
        job = claim_next(self.db)
        result = self.complete_with_mock_detector(job, [25, 100])
        self.assertEqual(video.read_bytes(), original)
        self.assertEqual(queue_summary(self.db)["jobs"], {"SUCCEEDED": 1})
        self.assertEqual(json.loads(Path(result).read_text())["results"][0]["target"][0]["start_frame"], 25)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM detector_runs").fetchone()[0], 1)
        self.assertTrue(Path(result).is_file())

    def test_detector_rejects_changed_config_and_source(self):
        video, sidecar = self.make_segment()
        enqueue_verified(self.db, self.cfg_sha)
        job = claim_next(self.db)
        self.config.write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "config changed"):
            self.complete_with_mock_detector(job, [5])
        self.config.write_text("detector: sample_test_config\n", encoding="utf-8")
        video.write_bytes(b"tampered original")
        with self.assertRaisesRegex(RuntimeError, "changed since ingest"):
            self.complete_with_mock_detector(job, [5])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM detector_runs").fetchone()[0], 0)

    def test_pipeline_cli_init_enqueue_status_events(self):
        self.make_segment()
        self.db.close()
        # This test opens a new connection through the public entrypoint.
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(meteormesh_pipeline.main(["--data-dir", str(self.root), "init"]), 0)
            self.assertEqual(meteormesh_pipeline.main(["--data-dir", str(self.root), "enqueue",
                                                        "--metdet-config", str(self.config)]), 0)
            self.assertEqual(meteormesh_pipeline.main(["--data-dir", str(self.root), "status"]), 0)
            report = self.root / "events.json"
            self.assertEqual(meteormesh_pipeline.main(["--data-dir", str(self.root), "events",
                                                       "--output", str(report)]), 0)
        self.assertFalse(json.loads(report.read_text())["auto_delete"])
        self.assertEqual(json.loads(report.read_text())["provisional_groups"], 0)
        with self.assertRaises(FileExistsError):
            with contextlib.redirect_stdout(io.StringIO()):
                meteormesh_pipeline.main(["--data-dir", str(self.root), "events", "--output", str(report)])


class EventTests(BasePipelineTest):
    def test_time_parser(self):
        self.assertEqual(parse_relative_time("00:00:01.25"), 1250)
        self.assertEqual(parse_relative_time("01:02:03.004"), 3_723_004)
        with self.assertRaises(ValueError):
            parse_relative_time("00:99:03")

    def test_mdrf_candidate_is_explicitly_unverified(self):
        _, sidecar = self.make_segment()
        candidate = candidates_from_mdrf("ruby", sidecar, self.mdrf([25]), "a" * 64)[0]
        self.assertEqual(candidate.utc_ms_estimate, 1_001_000)
        self.assertIn("UNVERIFIED", candidate.time_quality)

    def test_multi_camera_group_not_confirmed(self):
        r = self.make_segment("ruby", "r001")[1]
        v = self.make_segment("viva", "v001")[1]
        sr = candidates_from_mdrf("ruby", r, self.mdrf([25, 50]), "a" * 64)
        sv = candidates_from_mdrf("viva", v, self.mdrf([30]), "b" * 64)
        groups = group_provisional(sr + sv)
        self.assertEqual(len(groups), 2)
        self.assertTrue(any(len(item["cameras"]) == 2 for item in groups))
        self.assertTrue(all(item["confirmed_same_meteor"] is False for item in groups))

    def test_single_camera_not_vetoed_and_neighbor_context(self):
        ruby = self.make_segment("ruby", "r001", started=1_000_000)[1]
        self.make_segment("ruby", "r002", started=1_300_000)
        self.make_segment("viva", "v001", started=1_000_000)
        self.make_segment("surya", "s001", started=1_000_000)
        enqueue_verified(self.db, self.cfg_sha)
        job = claim_next(self.db)
        self.complete_with_mock_detector(job, [7490])  # 299.6 seconds, near boundary
        report = build_event_report(self.db, self.root, ["ruby", "viva", "surya"])
        self.assertEqual(report["provisional_groups"], 1)
        e = report["events"][0]
        self.assertEqual(e["cameras"], ["ruby"])
        self.assertFalse(e["confirmed_same_meteor"])
        self.assertFalse(e["archive_verified"])
        self.assertFalse(e["retention_permission"])
        self.assertEqual(len(e["source_context"]["ruby"]), 2)
        self.assertEqual(len(e["source_context"]["viva"]), 1)

    def test_unknown_time_keeps_only_own_source(self):
        r = self.make_segment("ruby", "r001")[1]
        self.make_segment("ruby", "r002", started=1_300_000)
        self.make_segment("viva", "v001")
        r.pop("start_utc_ms_estimate")
        candidate = candidates_from_mdrf("ruby", r, self.mdrf([10]), "a" * 64)[0]
        event = group_provisional([candidate])[0]
        output = context_for_event(event, self.root, ["ruby", "viva"])
        self.assertEqual(output["utc_ms_estimate"], None)
        self.assertEqual([a["segment_id"] for a in output["source_context"]["ruby"]], ["r001"])
        self.assertEqual(output["source_context"]["viva"], [])

    def test_unknown_time_and_absent_sidecar_clock_still_keep_original(self):
        _, r = self.make_segment("ruby", "r001")
        path = self.root / "ruby" / "r001.json"
        r.pop("start_utc_ms_estimate")
        r.pop("end_utc_ms_estimate")
        path.write_text(json.dumps(r))
        candidate = candidates_from_mdrf("ruby", r, self.mdrf([10]), "a" * 64)[0]
        event = group_provisional([candidate])[0]
        context = context_for_event(event, self.root, ["ruby", "viva"])
        self.assertEqual([item["segment_id"] for item in context["source_context"]["ruby"]], ["r001"])
        self.assertIsNone(context["source_context"]["ruby"][0]["start_utc_ms_estimate"])

    def test_event_report_rejects_original_changed_after_detector(self):
        video, _ = self.make_segment()
        enqueue_verified(self.db, self.cfg_sha)
        self.complete_with_mock_detector(claim_next(self.db), [25])
        video.write_bytes(b"tampered source, retained for manual review")
        with self.assertRaisesRegex(RuntimeError, "Original video changed"):
            build_event_report(self.db, self.root, ["ruby"])

    def test_event_report_skips_source_later_downgraded_from_verified(self):
        _, source = self.make_segment()
        enqueue_verified(self.db, self.cfg_sha)
        self.complete_with_mock_detector(claim_next(self.db), [25])
        store_verified_segment(self.db, "ruby", source, "FFPROBE_FAILED", None)
        report = build_event_report(self.db, self.root, ["ruby"])
        self.assertEqual(report["provisional_groups"], 0)

    def test_result_tamper_is_rejected(self):
        self.make_segment()
        enqueue_verified(self.db, self.cfg_sha)
        result = Path(self.complete_with_mock_detector(claim_next(self.db), [25]))
        result.write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "Detector result changed"):
            build_event_report(self.db, self.root, ["ruby", "viva", "surya"])


class ReadOnlyAgentTests(BasePipelineTest):
    def test_agent_queries_have_no_write_authority(self):
        self.make_segment()
        enqueue_verified(self.db, self.cfg_sha)
        with meteor_agent.read_only_connection(self.root / "meteorhub.sqlite3") as ro:
            self.assertFalse(meteor_agent.answer(ro, "status")["agent_can_delete"])
            self.assertEqual(meteor_agent.answer(ro, "cameras")["cameras"][0]["id"], "ruby")
            self.assertEqual(len(meteor_agent.answer(ro, "pending")["jobs"]), 1)
            self.assertFalse(meteor_agent.answer(ro, "retention")["auto_purge"])
            with self.assertRaises(sqlite3.OperationalError):
                ro.execute("DELETE FROM jobs")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)

    def test_agent_rejects_arbitrary_query(self):
        with meteor_agent.read_only_connection(self.root / "meteorhub.sqlite3") as ro:
            with self.assertRaises(ValueError):
                meteor_agent.answer(ro, "rm -rf *")


if __name__ == "__main__":
    unittest.main()
