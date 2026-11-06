import unittest
from pathlib import Path

P = Path(__file__).parent
JAVA = P/'app/src/main/java/org/meteormesh/node'

class TestStorageContracts(unittest.TestCase):
    def test_version_and_permissions(self):
        g=(P/'app/build.gradle.kts').read_text()
        m=(P/'app/src/main/AndroidManifest.xml').read_text()
        self.assertIn('versionCode = 19',g)
        self.assertIn('MANAGE_EXTERNAL_STORAGE',m)
        self.assertIn('applicationId = "org.meteormesh.node.compactfixedtest"',g)
    def test_endpoint_surface(self):
        s=(JAVA/'LocalHttpServer.kt').read_text()
        for route in ('list','file','upload','mkdir','move','delete','delete-tree','purge-recordings'):
            self.assertIn('/api/v1/storage/'+route,s)
        self.assertIn('X-Meteor-Storage-Key'.lower(), s.lower())
        self.assertIn('MessageDigest.isEqual(supplied, key)',s)
    def test_android_protection_and_no_shell(self):
        s=(JAVA/'RemoteStorage.kt').read_text()
        self.assertIn('parts.first().equals("Android"',s)
        self.assertIn('!parts[1].equals("media"',s)
        self.assertIn('path_escape_or_symlink',s)
        self.assertIn('root_operation_forbidden',s)
        self.assertIn('purgeVerified', (JAVA/'SegmentStore.kt').read_text())
        self.assertIn('automatic_delete_enabled", false',s)
        for forbidden in ('Runtime.getRuntime().exec', 'ProcessBuilder(', 'android.permission.QUERY_ALL_PACKAGES'):
            self.assertNotIn(forbidden, s)
    def test_authorization_and_permission(self):
        s=(JAVA/'MainActivity.kt').read_text()
        self.assertIn('ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION',s)
        self.assertIn('RemoteStorage.adminKey(this)',s)
    def test_live_original_routes(self):
        s=(JAVA/'LocalHttpServer.kt').read_text()
        for route in ('/api/v1/control/start','/api/v1/control/stop','/api/v1/segments','/api/v1/health'):
            self.assertIn(route,s)
    def test_cli_explicit_operations(self):
        s=(P/'linux/meteor_storage.py').read_text()
        self.assertIn("'rmtree'",s)
        self.assertIn("'replace'",s)
        self.assertIn('chmod 600',s)

if __name__=='__main__': unittest.main()
