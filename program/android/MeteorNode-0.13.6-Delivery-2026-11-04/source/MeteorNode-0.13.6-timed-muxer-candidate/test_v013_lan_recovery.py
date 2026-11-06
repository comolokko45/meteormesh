import unittest
from pathlib import Path
P=Path(__file__).parent
J=P/'app/src/main/java/org/meteormesh/node'

class TestLanRecovery(unittest.TestCase):
    def test_not_datasync_service(self):
        text=(P/'app/src/main/AndroidManifest.xml').read_text()
        self.assertIn('foregroundServiceType="connectedDevice"',text)
        self.assertIn('android.permission.FOREGROUND_SERVICE_CONNECTED_DEVICE',text)
        self.assertIn('android.permission.CHANGE_NETWORK_STATE',text)
        self.assertNotIn('foregroundServiceType="dataSync"',text)
    def test_bounded_watchdog(self):
        text=(J/'MeteorLanService.kt').read_text()
        self.assertIn('START_STICKY',text)
        self.assertIn('RemoteSettings.enabled(this@MeteorLanService)',text)
        self.assertIn('isListening()',text)
        self.assertIn('30_000L * (1L shl exponent)',text)
        self.assertIn('handler.removeCallbacks(watchdog)',text)
        self.assertNotIn('MeteorCaptureService.remoteStart',text)
    def test_versions_and_compatibility(self):
        h=(J/'LocalHttpServer.kt').read_text()
        self.assertIn('0.10.12-v013-storage-compatible',h)
        self.assertIn('BuildConfig.VERSION_NAME',h)
        self.assertIn('APP_PRIVATE_SINGLE_COPY',h)
        self.assertIn('fun isListening()',h)
        self.assertIn('/api/v1/segments',h)
    def test_previous_single_copy(self):
        original=(J/'SegmentStore.kt').read_text()
        self.assertNotIn('PublicVideoExporter.publish(context, complete)',original)
        self.assertIn('segment.partialFile.renameTo(complete)',original)
    def test_app_id_same_for_update(self):
        gradle=(P/'app/build.gradle.kts').read_text()
        self.assertIn('versionCode = 19',gradle)
        self.assertIn('applicationId = "org.meteormesh.node.compactfixedtest"',gradle)

if __name__=='__main__': unittest.main()
