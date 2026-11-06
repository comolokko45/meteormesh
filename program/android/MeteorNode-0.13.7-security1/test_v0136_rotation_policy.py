import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parent
JAVA = ROOT / "app/src/main/java/org/meteormesh/node"


class RotationPolicyContract(unittest.TestCase):
    def test_nominal_and_emergency_limits_are_separate(self):
        policy = (JAVA / "RecordingPolicy.kt").read_text()
        self.assertIn("fun expectedSegmentBytes", policy)
        self.assertIn("fun emergencyFileLimitBytes", policy)
        self.assertIn("EMERGENCY_MARGIN_DIVISOR = 4L", policy)

        bitrate = 6_000_000
        duration_ms = 300_000
        expected = bitrate * duration_ms // 8_000
        emergency = expected + (expected + 3) // 4
        self.assertEqual(expected, 225_000_000)
        self.assertEqual(emergency, 281_250_000)
        self.assertGreater(emergency, expected)

    def test_normal_rollover_is_pts_driven(self):
        service = (JAVA / "MeteorCaptureService.kt").read_text()
        self.assertIn("val durationReached = ptsUs - segmentStart", service)
        self.assertIn("val emergencySizeTrigger", service)
        self.assertIn('"emergency_size"', service)
        self.assertNotIn("ROTATION_SIZE_TRIGGER_PERCENT", service)
        self.assertNotRegex(service, re.compile(r"activeEmergencyFileLimitBytes\s*\*\s*90"))

    def test_storage_headroom_uses_emergency_ceiling(self):
        service = (JAVA / "MeteorCaptureService.kt").read_text()
        self.assertIn("requiredFreeBytes(emergencyFileLimitBytes)", service)
        self.assertIn("requiredFreeBytes(activeEmergencyFileLimitBytes)", service)
        self.assertIn('putLong("recording_segment_expected_bytes"', service)
        self.assertIn('putLong("recording_segment_emergency_limit_bytes"', service)

    def test_version_and_rotation_mode_are_consistent(self):
        gradle = (ROOT / "app/build.gradle.kts").read_text()
        health = (JAVA / "LocalHttpServer.kt").read_text()
        heartbeat = (JAVA / "HubHeartbeat.kt").read_text()
        settings = (JAVA / "RemoteSettings.kt").read_text()
        version = "0.13.6-pin0000-timed-muxer-beta1"
        mode = "MEDIACODEC_PTS_TIMED_OR_EMERGENCY_SIZE_SYNC_FRAME"
        self.assertIn('versionCode = 19', gradle)
        self.assertIn(version, gradle)
        for text in (health, heartbeat):
            self.assertIn("BuildConfig.VERSION_NAME", text)
        for text in (health, settings):
            self.assertIn(mode, text)

    def test_sidecar_identifies_build_and_rotation_reason(self):
        store = (JAVA / "SegmentStore.kt").read_text()
        service = (JAVA / "MeteorCaptureService.kt").read_text()
        self.assertIn('put("app_version", BuildConfig.VERSION_NAME)', store)
        self.assertIn('put("app_version_code", BuildConfig.VERSION_CODE)', store)
        self.assertIn('"MEDIACODEC_PTS_DURATION_SYNC_FRAME"', service)
        self.assertIn('"MEDIACODEC_EMERGENCY_SIZE_SYNC_FRAME"', service)
        self.assertIn('"MEDIACODEC_FINALIZE_STOP"', service)


if __name__ == "__main__":
    unittest.main()
