import importlib.util
import json
import unittest
from pathlib import Path
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import patch
import zoneinfo

SOURCE = Path(__file__).with_name("meteor-monitor-server.py")
spec = importlib.util.spec_from_file_location("meteor_monitor_rc188", SOURCE)
module = importlib.util.module_from_spec(spec)
with patch.object(zoneinfo, "ZoneInfo", return_value=timezone.utc):
    spec.loader.exec_module(module)


def event(message, stamp="1793828400000000"):
    return json.dumps({"MESSAGE": message, "__REALTIME_TIMESTAMP": stamp})


class AnalysisExecutionTests(unittest.TestCase):
    def test_source_missing_skip_and_backup_are_visible(self):
        props = "ActiveState=inactive\nResult=exec-condition\nExecMainStatus=0\nConditionResult=no\n"
        logs = "\n".join([
            event("Starting meteormesh-analyze.service - MeteorMesh analyzer..."),
            event('ANALYSIS_QUEUE_NO_ACTIONABLE_SOURCE: {"night_id":"20261103-night","eligible_jobs":99,"source_ready_jobs":0,"source_missing_jobs":99,"retired_jobs":0}'),
            event("MM153_DB_BACKUP: PASS"),
            event("BACKUP_PATH: /home/meteor/db-backups/meteorhub-test.sqlite3"),
            event("RC188_POST_ANALYSIS: SKIPPED_EXEC_CONDITION BACKUP=OK"),
        ])
        result = module.parse_analysis_execution(props, logs)
        self.assertEqual(result["state"], "SKIPPED_NO_SOURCE")
        self.assertEqual(result["source"]["source_missing_jobs"], 99)
        self.assertEqual(result["backup"], {"state": "PASS", "name": "meteorhub-test.sqlite3"})

    def test_source_ready_success_is_completed(self):
        props = "ActiveState=inactive\nResult=success\nExecMainStatus=0\n"
        logs = "\n".join([
            event("Starting meteormesh-analyze.service - MeteorMesh analyzer..."),
            event('ANALYSIS_SOURCE_READY: {"night_id":"20261104-night","eligible_jobs":5,"source_ready_jobs":5,"source_missing_jobs":0,"retired_jobs":0}'),
            event("Finished meteormesh-analyze.service - MeteorMesh analyzer."),
        ])
        result = module.parse_analysis_execution(props, logs)
        self.assertEqual(result["state"], "COMPLETED")
        self.assertEqual(result["source"]["source_ready_jobs"], 5)

    def test_active_service_is_running(self):
        result = module.parse_analysis_execution(
            "ActiveState=active\nResult=success\n", event("Starting meteormesh-analyze.service"))
        self.assertEqual(result["state"], "RUNNING")

    def test_analyzer_failure_is_not_reported_as_skip(self):
        result = module.parse_analysis_execution(
            "ActiveState=inactive\nResult=exit-code\n",
            "\n".join([event("Starting meteormesh-analyze.service"), event("MM153_DB_BACKUP: ERROR disk")]))
        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(result["backup"]["state"], "ERROR")

    def test_malformed_journal_source_fails_to_unknown_condition(self):
        props = "ActiveState=inactive\nResult=exec-condition\n"
        logs = "\n".join([event("Starting meteormesh-analyze.service"),
                           event("ANALYSIS_QUEUE_NO_ACTIONABLE_SOURCE: {bad")])
        self.assertEqual(module.parse_analysis_execution(props, logs)["state"], "SKIPPED_PRECONDITION")

    def test_read_keeps_latest_run_marker_when_posthook_has_many_lines(self):
        props = "ActiveState=inactive\nResult=success\nExecMainStatus=0\nConditionResult=yes\n"
        messages = [
            event("Starting meteormesh-analyze.service - MeteorMesh analyzer..."),
            event('ANALYSIS_SOURCE_READY: {"night_id":"20261104-night","eligible_jobs":399,"source_ready_jobs":300,"source_missing_jobs":99,"retired_jobs":0}'),
        ]
        messages.extend(event(f"RETENTION_SKIP cam-ruby/segment-{n}: reason") for n in range(150))
        messages.extend([
            event("MM153_DB_BACKUP: PASS"),
            event("BACKUP_PATH: /home/meteor/db-backups/meteorhub-latest.sqlite3"),
        ])
        commands = []

        def run_readonly(argv, timeout):
            commands.append(argv)
            if argv[0] == "systemctl":
                return SimpleNamespace(returncode=0, stdout=props, stderr="")
            count = int(argv[argv.index("-n") + 1])
            output = "\n".join(messages[-count:])
            return SimpleNamespace(returncode=0, stdout=output, stderr="")

        with patch.object(module, "run_readonly", side_effect=run_readonly):
            result = module.read_analysis_execution()

        self.assertEqual(commands[1][commands[1].index("-n") + 1], "500")
        self.assertEqual(result["source"]["source_ready_jobs"], 300)
        self.assertEqual(result["backup"], {"state": "PASS", "name": "meteorhub-latest.sqlite3"})

    def test_schedule_gate_shows_today_predawn_as_partial_and_morning_as_pending(self):
        now = module.datetime.fromisoformat("2026-11-05T08:25:00+03:00")
        gates = module.natural_timer_gates(
            [
                {"unit": "meteormesh-predawn.timer", "last_local": "2026-11-05T05:30:00+03:00",
                 "next_local": "2026-11-06T05:30:00+03:00"},
                {"unit": "meteormesh-morning.timer", "last_local": "2026-11-04T09:00:00+03:00",
                 "next_local": "2026-11-05T09:00:00+03:00"},
            ],
            {"predawn": {"state": "inactive", "result": "success"},
             "morning": {"state": "inactive", "result": "success"}},
            {"state": "RUNNING", "started_local": "2026-11-05T08:25:20+03:00",
             "source": {"night_id": "20261104-night", "source_ready_jobs": 295,
                        "source_missing_jobs": 99}},
            {"items": []}, "20261104-night", now,
        )
        self.assertEqual(gates["predawn"]["status"], "PARTIAL")
        self.assertIn("source-ready=295", gates["predawn"]["evidence"])
        self.assertEqual(gates["morning"]["status"], "BLOCKED")

    def test_prior_day_timers_and_reports_do_not_count_for_today(self):
        now = module.datetime.fromisoformat("2026-11-05T09:15:00+03:00")
        gates = module.natural_timer_gates(
            [
                {"unit": "meteormesh-predawn.timer", "last_local": "2026-11-04T05:30:00+03:00",
                 "next_local": "2026-11-05T05:30:00+03:00"},
                {"unit": "meteormesh-morning.timer", "last_local": "2026-11-04T09:00:00+03:00",
                 "next_local": "2026-11-05T09:00:00+03:00"},
            ],
            {"predawn": {"state": "inactive", "result": "success"},
             "morning": {"state": "inactive", "result": "success"}},
            {"state": "COMPLETED", "started_local": "2026-11-04T08:00:00+03:00",
             "source": {"night_id": "20261103-night"}},
            {"items": [{"night_id": "20261103-night", "finished_local": "2026-11-04T09:05:00+03:00"}]},
            "20261104-night", now,
        )
        self.assertEqual(gates["predawn"]["status"], "BLOCKED")
        self.assertEqual(gates["morning"]["status"], "BLOCKED")

    def test_today_morning_report_is_attached_to_current_capture_night(self):
        now = module.datetime.fromisoformat("2026-11-05T09:15:00+03:00")
        gates = module.natural_timer_gates(
            [{"unit": "meteormesh-morning.timer", "last_local": "2026-11-05T09:00:00+03:00",
              "next_local": "2026-11-06T09:00:00+03:00"}],
            {"morning": {"state": "inactive", "result": "success"}},
            {},
            {"items": [{"night_id": "20261104-night", "finished_local": "2026-11-05T09:04:00+03:00",
                        "phase": "sync", "outcome": "PARTIAL_REVIEW_REQUIRED", "sync_good": 3}]},
            "20261104-night", now,
        )
        self.assertEqual(gates["morning"]["status"], "PARTIAL")
        self.assertIn("sync_good=3", gates["morning"]["evidence"])

    def test_morning_gate_uses_sync_report_even_after_later_analysis_reports(self):
        now = module.datetime.fromisoformat("2026-11-05T09:40:00+03:00")
        night_id = "20261104-night"
        gates = module.natural_timer_gates(
            [{"unit": "meteormesh-morning.timer", "last_local": "2026-11-05T09:00:00+03:00",
              "next_local": "2026-11-06T09:00:00+03:00"}],
            {"morning": {"state": "inactive", "result": "success"}}, {},
            {"items": [
                {"name": "analysis.json", "night_id": night_id,
                 "finished_local": "2026-11-05T09:32:33+03:00", "phase": "analyze",
                 "outcome": "ANALYSIS_BATCH_PENDING", "sync_good": 0}],
             "today_sync_reports": [
                {"name": "pre_timer_sync.json", "night_id": night_id,
                 "finished_local": "2026-11-05T08:52:06+03:00", "phase": "sync",
                 "outcome": "PARTIAL_REVIEW_REQUIRED", "sync_good": 0},
                {"name": "sync.json", "night_id": night_id,
                 "finished_local": "2026-11-05T09:10:04+03:00", "phase": "sync",
                 "outcome": "PARTIAL_REVIEW_REQUIRED", "sync_good": 0},
                {"name": "later_sync.json", "night_id": night_id,
                 "finished_local": "2026-11-05T09:37:06+03:00", "phase": "sync",
                 "outcome": "PARTIAL_REVIEW_REQUIRED", "sync_good": 0}]},
            night_id, now,
        )
        self.assertEqual(gates["morning"]["status"], "PARTIAL")
        self.assertIn("PARTIAL_REVIEW_REQUIRED", gates["morning"]["evidence"])
        self.assertIn("09:10:04", gates["morning"]["evidence"])
        self.assertNotIn("ANALYSIS_BATCH_PENDING", gates["morning"]["evidence"])
        self.assertNotIn("09:37:06", gates["morning"]["evidence"])

    def test_predawn_progress_survives_a_later_skipped_analyzer_round(self):
        now = module.datetime.fromisoformat("2026-11-05T09:15:00+03:00")
        night_id = "20261104-night"
        gates = module.natural_timer_gates(
            [{"unit": "meteormesh-predawn.timer", "last_local": "2026-11-05T05:30:00+03:00",
              "next_local": "2026-11-06T05:30:00+03:00"}],
            {"predawn": {"state": "inactive", "result": "success"}},
            {"state": "SKIPPED_PRECONDITION", "started_local": "2026-11-05T09:10:02+03:00",
             "source": None},
            {"items": []}, night_id, now,
            {"night_id": night_id, "analysis_queue":
             {"WAITING": 280, "RUNNING": 0, "SUCCEEDED": 75, "FAILED": 0}},
        )
        self.assertEqual(gates["predawn"]["status"], "PARTIAL")
        self.assertIn("SUCCEEDED=75", gates["predawn"]["evidence"])
        self.assertNotIn("başlangıç kanıtı henüz eşleşmedi", gates["predawn"]["evidence"])


class RetentionAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.cleanup = {
            "auto_delete_enabled": True,
            "auto_delete_enabled_utc": "2026-11-05T08:54:14.916775+00:00",
            "last_deleted_utc": "2026-11-05T08:09:21.360511+00:00",
            "deleted_video_count": 1,
            "max_deletes_per_analysis_hook": 5,
        }
        self.dispositions = {"NO_CANDIDATE_DELETED": 1}

    def test_pilot_passes_software_gate_but_not_post_activation_operation(self):
        result = module.retention_acceptance(self.cleanup, self.dispositions)
        self.assertEqual(result["software_status"], "PASS")
        self.assertEqual(result["operations_status"], "PARTIAL")
        self.assertFalse(result["post_activation_delete_confirmed"])

    def test_natural_post_activation_delete_passes_operations_gate(self):
        cleanup = dict(self.cleanup, last_deleted_utc="2026-11-05T09:02:00+00:00")
        result = module.retention_acceptance(cleanup, self.dispositions)
        self.assertEqual(result["operations_status"], "PASS")
        self.assertTrue(result["post_activation_delete_confirmed"])

    def test_disabled_policy_blocks_automatic_operation(self):
        cleanup = dict(self.cleanup, auto_delete_enabled=False,
                       auto_delete_enabled_utc=None)
        result = module.retention_acceptance(cleanup, self.dispositions)
        self.assertEqual(result["operations_status"], "BLOCKED")
        self.assertEqual(result["software_status"], "PARTIAL")

    def test_invalid_or_missing_timestamps_never_pass_operations(self):
        cleanup = dict(self.cleanup, auto_delete_enabled_utc="invalid",
                       last_deleted_utc="2026-11-05T09:20:00+00:00")
        result = module.retention_acceptance(cleanup, self.dispositions)
        self.assertEqual(result["operations_status"], "PARTIAL")
        self.assertFalse(result["post_activation_delete_confirmed"])

    def test_uncapped_policy_or_missing_pilot_does_not_pass_software_gate(self):
        cleanup = dict(self.cleanup, max_deletes_per_analysis_hook=6)
        self.assertEqual(module.retention_acceptance(cleanup, self.dispositions)
                         ["software_status"], "PARTIAL")
        self.assertEqual(module.retention_acceptance(self.cleanup, {})
                         ["software_status"], "PARTIAL")

    def test_missing_or_malformed_policy_marker_fails_closed(self):
        with patch.object(module, "RETENTION_POLICY", Path("missing-retention-policy.json")):
            result = module.read_retention_policy()
        self.assertFalse(result["auto_delete_enabled"])
        self.assertEqual(result["policy_state"], "UNAVAILABLE")

    def test_live_policy_marker_is_read_without_mutation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "policy.json"
            marker.write_text(json.dumps({
                "schema": "meteormesh.negative-retention-activation.v1",
                "auto_delete_enabled": True,
                "auto_delete_enabled_utc": "2026-11-05T08:54:14+00:00",
            }), encoding="utf-8")
            with patch.object(module, "RETENTION_POLICY", marker):
                result = module.read_retention_policy()
            self.assertTrue(result["auto_delete_enabled"])
            self.assertEqual(result["policy_state"], "READ_OK")
            self.assertEqual(result["auto_delete_enabled_utc"],
                             "2026-11-05T08:54:14+00:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
