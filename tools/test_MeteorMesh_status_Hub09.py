"""Isolated regression tests for the read-only MeteorMesh status tool."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_SOURCE = Path(__file__).with_name("MeteorMesh-status-Hub09.py")
_SPEC = importlib.util.spec_from_file_location("meteor_status_hub09", _SOURCE)
assert _SPEC and _SPEC.loader
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


class StatusTests(unittest.TestCase):
    def test_no_fallback_when_data_disk_is_unmounted(self):
        with tempfile.TemporaryDirectory() as folder:
            result = mod.storage_details(Path(folder), require_mount=True)
        self.assertFalse(result["is_mount"])
        self.assertIsNone(result["free_gib"])
        self.assertEqual(result["reason"], "NOT_MOUNTED_NO_FALLBACK_TO_SSD")

    def test_no_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(mod.sqlite_details(root)["state"], "NOT_CREATED")
            self.assertFalse((root / "meteorhub.sqlite3").exists())

    def test_sqlite_is_read_only_and_counts_states(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "meteorhub.sqlite3"
            db = sqlite3.connect(path)
            db.executescript("""
                CREATE TABLE segments(state TEXT);
                INSERT INTO segments VALUES ('INGEST_VERIFIED');
                CREATE TABLE jobs(state TEXT);
                INSERT INTO jobs VALUES ('FAILED');
                INSERT INTO jobs VALUES ('SUCCEEDED');
                CREATE TABLE detector_runs(id INTEGER);
                INSERT INTO detector_runs VALUES (1);
            """)
            db.commit()
            db.close()
            result = mod.sqlite_details(Path(folder))
            self.assertEqual(result["state"], "READ_ONLY_OK")
            self.assertEqual(result["ingest"], {"INGEST_VERIFIED": 1})
            self.assertEqual(result["jobs"], {"FAILED": 1, "SUCCEEDED": 1})
            self.assertEqual(result["detector_runs"], 1)
            self.assertEqual(sorted(x.name for x in Path(folder).iterdir()),
                             ["meteorhub.sqlite3"])

    def test_read_latest_report_does_not_echo_error_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            reports = Path(folder) / "morning-reports"
            reports.mkdir()
            (reports / "morning_20261029.json").write_text(json.dumps({
                "outcome": "PARTIAL_REVIEW_REQUIRED",
                "errors": ["DO_NOT_PRINT_SECRET_TOKEN"],
                "finished_utc": "2026-10-29T00:00:00Z"}), encoding="utf-8")
            result = mod.morning_details(Path(folder))
            self.assertEqual(result["error_count"], 1)
            self.assertNotIn("DO_NOT_PRINT_SECRET_TOKEN", repr(result))

    def _fixed_results(self, *, mounted=True, failed=0, partial=False, free=435):
        hdd = {"path": "/srv/meteor-data", "exists": mounted,
               "is_mount": mounted, "free_gib": free if mounted else None,
               "total_gib": 458}
        ssd = {"path": "/", "exists": True, "is_mount": True,
               "free_gib": 85, "total_gib": 98}
        db = {"state": "READ_ONLY_OK", "jobs": {"FAILED": failed} if failed else {},
              "ingest": {}, "detector_runs": 0}
        report = {"state": "READ_OK" if partial else "NO_REPORT_YET",
                  "outcome": "PARTIAL_REVIEW_REQUIRED" if partial else None}
        def storage(path, *, require_mount):
            return hdd if require_mount else ssd
        def command(argv, timeout=3):
            if argv[0] == "systemctl":
                return {"ok": True, "text": "LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled"}
            if argv[0] == "findmnt":
                return {"ok": True, "text": "/dev/sdb1 ext4 444a8a6d-090a-456c-8a9d-d99665c32574"}
            if argv[0] == "loginctl":
                return {"ok": True, "text": "Linger=yes"}
            return {"ok": True, "text": "NTPSynchronized=yes"}
        return storage, command, db, report

    def test_ready_state_before_phones_is_not_false_alarm(self):
        storage, command, db, report = self._fixed_results()
        with patch.object(mod, "storage_details", side_effect=storage), \
             patch.object(mod, "read_command", side_effect=command), \
             patch.object(mod, "sqlite_details", return_value={"state": "NOT_CREATED"}), \
             patch.object(mod, "morning_details", return_value=report):
            data = mod.collect()
        self.assertEqual(data["status"], "SETUP_WAITING_FOR_PHONES")
        self.assertFalse(data["issues"])
        self.assertFalse(data["can_delete"])

    def test_failed_jobs_and_partial_report_are_warnings(self):
        storage, command, db, report = self._fixed_results(failed=2, partial=True)
        with patch.object(mod, "storage_details", side_effect=storage), \
             patch.object(mod, "read_command", side_effect=command), \
             patch.object(mod, "sqlite_details", return_value=db), \
             patch.object(mod, "morning_details", return_value=report):
            data = mod.collect()
        self.assertEqual(data["status"], "WARNING")
        self.assertEqual({x["code"] for x in data["issues"]},
                         {"DETECTOR_FAILED_JOBS", "LAST_MORNING_PARTIAL"})

    def test_unmounted_hdd_is_critical_without_db_access(self):
        storage, command, db, report = self._fixed_results(mounted=False)
        with patch.object(mod, "storage_details", side_effect=storage), \
             patch.object(mod, "read_command", side_effect=command), \
             patch.object(mod, "sqlite_details") as db_reader:
            data = mod.collect()
        self.assertEqual(data["status"], "CRITICAL")
        db_reader.assert_not_called()
        self.assertEqual(data["issues"][0]["code"], "HDD_NOT_MOUNTED")

    def test_unexpected_hdd_uuid_fails_closed(self):
        storage, command, db, report = self._fixed_results()
        def wrong_uuid(argv, timeout=3):
            if argv[0] == "findmnt":
                return {"ok": True, "text": "/dev/sdb1 ext4 00000000-0000-0000-0000-000000000000"}
            return command(argv, timeout)
        with patch.object(mod, "storage_details", side_effect=storage), \
             patch.object(mod, "read_command", side_effect=wrong_uuid), \
             patch.object(mod, "sqlite_details", return_value=db), \
             patch.object(mod, "morning_details", return_value=report):
            data = mod.collect()
        self.assertEqual(data["status"], "CRITICAL")
        self.assertIn("HDD_UNEXPECTED_UUID", {x["code"] for x in data["issues"]})


if __name__ == "__main__":
    unittest.main()
