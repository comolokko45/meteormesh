import pathlib,unittest,hashlib
P=pathlib.Path(__file__).parent
J=P/'app/src/main/java/org/meteormesh/node'
CERT='aa2f35e659a795bbd50ba8fade8de916ae7308c5cef3247a3fba74683c282c7b'
class Contract(unittest.TestCase):
 def test_pin_and_cert(self):
  s=(J/'HubControlConfig.kt').read_text()
  self.assertIn('SHARED_PIN = "0000"',s)
  self.assertIn(CERT,s)
  self.assertIn('meteormesh.control013.pin.v1:$device:$SHARED_PIN',s)
  self.assertIn('pin.trim() != SHARED_PIN',s)
  self.assertIn('MessageDigest.getInstance("SHA-256")',s)
  self.assertIn('token != perCameraToken(deviceId()!!)',s)
  for dev in ('cam-viva','cam-ruby','cam-surya'):
   self.assertEqual(len(hashlib.sha256(f'meteormesh.control013.pin.v1:{dev}:0000'.encode()).hexdigest()),64)
 def test_ui_simple(self):
  s=(J/'MainActivity.kt').read_text()
  self.assertIn('setText("0000")',s)
  self.assertIn('HubControlConfig.save(this,ip.text.toString(),pin.text.toString())',s)
  self.assertNotIn('TLS CERT_SHA256 (64 karakter)',s)
  self.assertNotIn('DEVICE_TOKEN (64 karakter)',s)
 def test_preserve_pinned_tls_and_response(self):
  s=(J/'HubHeartbeat.kt').read_text()
  self.assertIn('MessageDigest.isEqual(fingerprint(leaf),pin)',s)
  self.assertIn('Authorization","Bearer ${cfg.token}',s)
  self.assertIn('restart_listener',s)
  self.assertIn('BuildConfig.VERSION_NAME',s)
 def test_preserve_recordings(self):
  s=(J/'SegmentStore.kt').read_text()
  self.assertNotIn('PublicVideoExporter.publish(context, complete)',s)
  self.assertIn('segment.partialFile.renameTo(complete)',s)
 def test_app_id(self):
  s=(P/'app/build.gradle.kts').read_text()
  self.assertIn('applicationId = "org.meteormesh.node.compactfixedtest"',s)
  self.assertIn('versionCode = 19',s)
 def test_no_untracked_apk_inside_source(self):
  unexpected=[p for p in P.rglob('*.apk') if 'build' not in p.parts]
  self.assertEqual(unexpected,[])
if __name__=='__main__':unittest.main()
