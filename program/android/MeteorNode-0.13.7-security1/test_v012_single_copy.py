import unittest
from pathlib import Path
P=Path(__file__).parent
J=P/'app/src/main/java/org/meteormesh/node'
class TestSingleCopy(unittest.TestCase):
    def test_one_source_only(self):
        code=(J/'SegmentStore.kt').read_text()
        self.assertNotIn('PublicVideoExporter.publish(context, complete)',code)
        self.assertIn('segment.partialFile.renameTo(complete)',code)
        self.assertIn('temp.renameTo(metadata)',code)
        self.assertIn('fun purgeVerified',code)
    def test_ingest_compatible(self):
        code=(J/'LocalHttpServer.kt').read_text()
        self.assertIn('0.10.12-',code)
        self.assertIn('APP_PRIVATE_SINGLE_COPY',code)
        for api in ('/api/v1/segments','/api/v1/health','/api/v1/storage/delete'):
            self.assertIn(api,code)
    def test_manifest_version(self):
        self.assertIn('versionCode = 19',(P/'app/build.gradle.kts').read_text())
        self.assertIn('MeteorNode 0.13',(P/'app/src/main/AndroidManifest.xml').read_text())
if __name__=='__main__': unittest.main()
