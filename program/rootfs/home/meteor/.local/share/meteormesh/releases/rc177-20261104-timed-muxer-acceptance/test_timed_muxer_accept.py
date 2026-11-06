import json
import tempfile
import unittest
from pathlib import Path

from meteor_timed_muxer_accept import Segment, inspect_boundary, load_segments


def metadata(segment_id: str, start: int, end: int, start_utc: int, end_utc: int) -> dict:
    return {
        "segment_id": segment_id,
        "device_id": "ruby_tr",
        "app_version": "0.13.6-pin0000-timed-muxer-beta1",
        "app_version_code": 19,
        "segmentation": "MEDIACODEC_PTS_DURATION_SYNC_FRAME",
        "media_presentation_start_us": start,
        "media_presentation_end_us": end,
        "start_utc_ms_estimate": start_utc,
        "end_utc_ms_estimate": end_utc,
        "nominal_fps": 30,
    }


class AcceptanceTests(unittest.TestCase):
    def test_good_boundary_accepts_one_frame_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = Segment(root / "a.json", root / "a.mp4", metadata("a", 0, 299_966_667, 1000, 301000))
            b = Segment(root / "b.json", root / "b.mp4", metadata("b", 300_000_000, 599_966_667, 301000, 601000))
            result = inspect_boundary(a, b)
            self.assertTrue(result["pass"])
            self.assertEqual(result["global_pts_gap_us"], 33_333)

    def test_large_boundary_gap_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = Segment(root / "a.json", root / "a.mp4", metadata("a", 0, 299_000_000, 1000, 301000))
            b = Segment(root / "b.json", root / "b.mp4", metadata("b", 301_000_000, 600_000_000, 303000, 603000))
            result = inspect_boundary(a, b)
            self.assertFalse(result["pass"])
            self.assertIn("GLOBAL_PTS_BOUNDARY_GAP_OUT_OF_RANGE", result["failures"])
            self.assertIn("UTC_BOUNDARY_ESTIMATE_DISCONTINUITY", result["failures"])

    def test_loader_requires_expected_build_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = metadata("good", 0, 300_000_000, 1000, 301000)
            bad = {**metadata("old", 0, 300_000_000, 1000, 301000), "app_version": "0.13.5"}
            (root / "good.json").write_text(json.dumps(good))
            (root / "good.mp4").write_bytes(b"video")
            (root / "old.json").write_text(json.dumps(bad))
            (root / "old.mp4").write_bytes(b"video")
            segments, errors = load_segments(root, "0.13.6-pin0000-timed-muxer-beta1", "ruby_tr")
            self.assertEqual(errors, [])
            self.assertEqual([item.segment_id for item in segments], ["good"])


if __name__ == "__main__":
    unittest.main()
