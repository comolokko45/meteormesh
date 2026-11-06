package org.meteormesh.node
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
