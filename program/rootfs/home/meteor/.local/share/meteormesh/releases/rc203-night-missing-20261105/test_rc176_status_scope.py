import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


if os.name == "nt":
    sys.modules.setdefault("pwd", types.SimpleNamespace(getpwuid=lambda _uid: types.SimpleNamespace(pw_name="meteor")))


MODULE_PATH = Path(__file__).with_name("MeteorMesh-status-Hub09.py")
SPEC = importlib.util.spec_from_file_location("meteormesh_status_rc176", MODULE_PATH)
STATUS = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(STATUS)


class NightScopeStatusTests(unittest.TestCase):
    def test_latest_sync_inherits_source_missing_from_same_night_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "morning-reports"
            reports.mkdir()
            analysis = {
                "night_id": "20261103-night", "phase": "analyze",
                "outcome": "ANALYSIS_SOURCE_MISSING_REVIEW_REQUIRED", "errors": [],
                "night_queue": {"WAITING": 99, "RUNNING": 0, "SUCCEEDED": 195, "FAILED": 0},
                "carryover_queue": {"WAITING": 0, "RUNNING": 0, "SUCCEEDED": 502, "FAILED": 119},
                "historical_queue_excluded_from_night_status": True,
                "waiting_missing_source": {"total": 99},
            }
            sync = {
                "night_id": "20261103-night", "phase": "sync",
                "outcome": "PARTIAL_REVIEW_REQUIRED", "errors": ["no ready"],
                "night_queue": analysis["night_queue"],
                "carryover_queue": analysis["carryover_queue"],
                "historical_queue_excluded_from_night_status": True,
            }
            (reports / "morning_20261104T100000Z_a.json").write_text(json.dumps(analysis))
            (reports / "morning_20261104T101000Z_b.json").write_text(json.dumps(sync))
            result = STATUS.morning_details(root, current_night_id="20261103-night")
            self.assertTrue(result["scope_is_current"])
            self.assertEqual(result["phase"], "sync")
            self.assertEqual(result["waiting_source_missing"], 99)

    def test_historical_failed_does_not_become_current_failed(self):
        db = {"jobs": {"SUCCEEDED": 700, "WAITING": 99, "FAILED": 119}}
        report = {
            "state": "READ_OK", "scope_is_current": True,
            "historical_queue_excluded_from_night_status": True,
            "night_id": "20261103-night",
            "night_queue": {"SUCCEEDED": 195, "WAITING": 99, "RUNNING": 0, "FAILED": 0},
            "carryover_queue": {"SUCCEEDED": 502, "WAITING": 0, "RUNNING": 0, "FAILED": 119},
            "waiting_source_missing": 99,
        }
        scope = STATUS.analysis_scope_details(db, report)
        self.assertTrue(scope["trusted_current_night"])
        self.assertEqual(scope["current_night_failed"], 0)
        self.assertEqual(scope["waiting_source_missing"], 99)
        self.assertEqual(scope["waiting_actionable_estimate"], 0)
        self.assertEqual(scope["historical_failed_review_only"], 119)

    def test_stale_night_uses_safe_global_fallback(self):
        db = {"jobs": {"FAILED": 119}}
        report = {
            "state": "READ_OK", "scope_is_current": False,
            "historical_queue_excluded_from_night_status": True,
            "night_queue": {"FAILED": 0},
        }
        scope = STATUS.analysis_scope_details(db, report)
        self.assertFalse(scope["trusted_current_night"])
        self.assertIsNone(scope["current_night_failed"])
        self.assertEqual(scope["global_queue_observed"]["FAILED"], 119)


if __name__ == "__main__":
    unittest.main()
