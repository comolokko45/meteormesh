from pathlib import Path
import sys

root=Path(sys.argv[1])
src=root/'app/src/main/java/org/meteormesh/node'
local=src/'LocalHttpServer.kt'
s=local.read_text()
assert s.count('var contentType: String? = null')==1
s=s.replace('var contentType: String? = null','var contentType: String? = null\n            var storageKey: String? = null')
s=s.replace('"content-type" -> contentType = line.substringAfter(\':\').trim()', '"content-type" -> contentType = line.substringAfter(\':\').trim()\n                    "x-meteor-storage-key" -> storageKey = line.substringAfter(\':\').trim()')
assert s.count('if (method == "POST") {')==1
route='''            // Storage operations need a separate random admin key, not the known lab token.
            if (path.startsWith("/api/v1/storage/")) {
                val required = RemoteStorage.adminKey(context).toByteArray(StandardCharsets.UTF_8)
                val supplied = storageKey?.toByteArray(StandardCharsets.UTF_8)
                if (supplied == null || !MessageDigest.isEqual(supplied, required)) {
                    sendText(output, 403, "Forbidden", "application/json", "{\\"error\\":\\"storage_admin_key_required\\"}")
                    return
                }
                if (!RemoteStorage.granted()) {
                    sendText(output, 403, "Forbidden", "application/json", "{\\"error\\":\\"all_files_access_required\\"}")
                    return
                }
                try {
                    if (method == "GET" && path == "/api/v1/storage/list") {
                        val dir = java.net.URLDecoder.decode(request[1].substringAfter("?dir=", ""), "UTF-8")
                        sendText(output, 200, "OK", "application/json", RemoteStorage.inventory(dir).toString())
                    } else if (method == "POST" && path == "/api/v1/storage/delete") {
                        if (!RemoteSettings.enabled(context)) {
                            sendText(output, 403, "Forbidden", "application/json", "{\\"error\\":\\"remote_disabled\\"}")
                            return
                        }
                        val n = contentLength ?: -1
                        if (n !in 2..4096 || contentType?.substringBefore(';')?.trim() != "application/json") {
                            sendText(output, 400, "Bad Request", "application/json", "{\\"error\\":\\"invalid_body\\"}")
                            return
                        }
                        val body = ByteArray(n)
                        var offset = 0
                        while (offset < n) {
                            val take = input.read(body, offset, n-offset)
                            if (take <= 0) return
                            offset += take
                        }
                        val result = RemoteStorage.deleteBatch(JSONObject(String(body, StandardCharsets.UTF_8)))
                        sendText(output, 200, "OK", "application/json", result.toString())
                    } else {
                        sendText(output, 404, "Not Found", "application/json", "{\\"error\\":\\"not_found\\"}")
                    }
                } catch (e: Exception) {
                    sendText(output, 400, "Bad Request", "application/json", JSONObject().put("error", e.message ?: "storage_error").toString())
                }
                return
            }
'''
s=s.replace('            if (method == "POST") {',route+'            if (method == "POST") {',1)
s=s.replace('"0.10.0-compact-fixedkey-lan-test"','"0.11.0-remote-storage"')
s=s.replace(' * Remote control is explicitly enabled on the phone; no shell/system/delete API.', ' * Storage endpoints require a separate random admin key and Android All Files Access.')
local.write_text(s)

main=src/'MainActivity.kt'
s=main.read_text()
assert s.count('layout.addView(label("MeteorNode 0.10", 25f))')==1
s=s.replace('layout.addView(label("MeteorNode 0.10", 25f))','layout.addView(label("MeteorNode 0.11", 25f))')
needle='''        button("DURUMU YENILE") { refreshStatus() }'''
assert needle in s
s=s.replace(needle,'''        button("DEPOLAMA YONETIMI IZNI") {
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                openAndroidSettings(Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                    Uri.parse("package:$packageName")))
            } else {
                Toast.makeText(this, "Bu Android surumunde ek izin gerekmiyor", Toast.LENGTH_SHORT).show()
            }
        }
        button("DEPOLAMA YONETICISI ANAHTARINI GOR") {
            AlertDialog.Builder(this).setTitle("Sadece HP G62'de saklayin")
                .setMessage("Yalniz ilk kurulumda Linux'a girilecek. Bu anahtara sahip kisi ortak depolamadaki dosyalari silebilir.\\n\\n" + RemoteStorage.adminKey(this))
                .setPositiveButton("Kapat", null).show()
        }
        '''+needle,1)
main.write_text(s)

manifest=root/'app/src/main/AndroidManifest.xml'
s=manifest.read_text()
assert '<uses-permission android:name="android.permission.WRITE_SETTINGS" />' in s
s=s.replace('<uses-permission android:name="android.permission.WRITE_SETTINGS" />',
'''<uses-permission android:name="android.permission.WRITE_SETTINGS" />
    <uses-permission android:name="android.permission.MANAGE_EXTERNAL_STORAGE" />''')
s=s.replace('MeteorNode 0.10 LAN','MeteorNode 0.11 Storage')
manifest.write_text(s)

gradle=root/'app/build.gradle.kts'
s=gradle.read_text()
s=s.replace('versionCode = 10','versionCode = 11')
s=s.replace('versionName = "0.10.0-compact-fixedkey-lan-test"','versionName = "0.11.0-remote-storage"')
gradle.write_text(s)

(src/'RemoteStorage.kt').write_bytes(Path(__file__).with_name('RemoteStorage.kt').read_bytes())
print('PATCH_011: PASS')
