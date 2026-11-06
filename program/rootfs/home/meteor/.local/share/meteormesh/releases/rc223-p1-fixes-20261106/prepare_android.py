from pathlib import Path
import shutil
source=Path('MeteorNode-0.13.6-Delivery-2026-11-04/source/MeteorNode-0.13.6-timed-muxer-candidate');target=Path('MeteorNode-0.13.7-security1')
shutil.copytree(source,target,ignore=shutil.ignore_patterns('build','.gradle','.git'))
p=target/'app/src/main/java/org/meteormesh/node/RemoteStorage.kt';s=p.read_text(encoding='utf-8')
s=s.replace('        val result = JSONArray()\n        var deleted', '''        // Validate the entire request before any filesystem operation/deletion.
        for (i in 0 until items.length()) {
            val sha = items.getJSONObject(i).optString("sha256", "")
            require(sha.matches(Regex("[0-9a-f]{64}"))) { "sha256_required_64_lowercase_hex" }
        }
        val result = JSONArray()
        var deleted''')
s=s.replace('sha.isEmpty() || (sha.matches(Regex("[0-9a-f]{64}")) && sha == sha256(file))','sha == sha256(file) && file.length() == bytes && file.lastModified() == timestamp')
start=s.index('    fun deleteTree(');end=s.index('\n    fun ',start+5)
s=s[:start]+'''    fun deleteTree(@Suppress("UNUSED_PARAMETER") payload: JSONObject): JSONObject {
        throw IllegalArgumentException("verified_file_manifest_required_use_delete_batch")
    }
'''+s[end:];p.write_text(s,encoding='utf-8')
p=target/'app/build.gradle.kts';s=p.read_text(encoding='utf-8').replace('versionCode = 19','versionCode = 20').replace('0.13.6-pin0000-timed-muxer-beta1','0.13.7-pin0000-security1');s+='\ndependencies { testImplementation("junit:junit:4.13.2"); testImplementation("org.json:json:20240303") }\n';p.write_text(s,encoding='utf-8')
p=target/'app/src/test/java/org/meteormesh/node/DeleteSafetyTest.kt';p.parent.mkdir(parents=True,exist_ok=True);p.write_text('''package org.meteormesh.node
import org.junit.Test
import org.json.JSONObject
import org.json.JSONArray
class DeleteSafetyTest {
 @Test(expected = IllegalArgumentException::class)
 fun missingShaRejectedBeforeFilesystem() { RemoteStorage.deleteBatch(JSONObject().put("files", JSONArray().put(JSONObject().put("path", "example.mp4")))) }
 @Test(expected = IllegalArgumentException::class)
 fun emptyShaRejectedBeforeFilesystem() { RemoteStorage.deleteBatch(JSONObject().put("files", JSONArray().put(JSONObject().put("sha256", "")))) }
 @Test(expected = IllegalArgumentException::class)
 fun wholeBatchPreflight() { RemoteStorage.deleteBatch(JSONObject().put("files", JSONArray().put(JSONObject().put("sha256", "a".repeat(64))).put(JSONObject().put("sha256", "")))) }
 @Test(expected = IllegalArgumentException::class)
 fun treeCannotBypassHashes() { RemoteStorage.deleteTree(JSONObject()) }
}
''',encoding='utf-8')
