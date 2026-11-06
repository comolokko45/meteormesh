package org.meteormesh.node

import android.content.Context
import android.os.Build
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import java.util.concurrent.Executors

data class ActiveSegment(
    val id: String,
    val partialFile: File,
    val cameraId: String,
    val mode: String,
    val width: Int,
    val height: Int,
    val fps: Int,
    val bitrate: Int,
    val startedUtcMs: Long,
    val startedMonotonicNs: Long,
    val targetSegmentMs: Long,
    val boundaryStart: String
)

/** Only complete and SHA-256-indexed MP4 files are exposed through the LAN API. */
class SegmentStore private constructor(private val context: Context) {
    companion object {
        // The UI and foreground service share one recovery/finalization queue.
        // Creating a new worker on every status refresh would leak threads and
        // could race another worker while publishing *.json.tmp and *.mp4.
        @Volatile private var singleton: SegmentStore? = null
        private val safeSegmentId = Regex("[a-zA-Z0-9_-]{3,100}")

        fun get(context: Context): SegmentStore = singleton ?: synchronized(this) {
            singleton ?: SegmentStore(context.applicationContext).also { singleton = it }
        }
    }
    private val folder = File(context.filesDir, "segments").apply { mkdirs() }
    private val finalizeWorker = Executors.newSingleThreadExecutor { r ->
        Thread(r, "meteor-finalize").apply { isDaemon = true }
    }

    init {
        // Crash reconciliation must not block Android's foreground service startup.
        finalizeWorker.execute {
            recoverInterruptedFinalizations()
            rememberLatestCompleted()
        }
    }

    fun create(
        cameraId: String,
        mode: String,
        width: Int,
        height: Int,
        fps: Int,
        bitrate: Int,
        targetSegmentMs: Long,
        boundaryStart: String = "CAMERA_SESSION_START"
    ): ActiveSegment {
        val now = System.currentTimeMillis()
        val device = Build.DEVICE.lowercase(Locale.US).replace(Regex("[^a-z0-9_-]"), "_")
        val date = SimpleDateFormat("yyyyMMdd'T'HHmmss'Z'", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }.format(Date(now))
        // A random suffix prevents reusing a filename after a service/process restart.
        val id = "${device}_${date}_${UUID.randomUUID().toString().replace("-", "").take(12)}"
        return ActiveSegment(
            id, File(folder, "$id.recording"), cameraId, mode, width, height, fps,
            bitrate, now, SystemClock.elapsedRealtimeNanos(), targetSegmentMs, boundaryStart
        )
    }

    fun finalizeAsync(
        segment: ActiveSegment,
        exposureFirstNs: Long?,
        exposureLastNs: Long?,
        captureResultCount: Long,
        sensorTimestampSource: String,
        endedUtcMs: Long = System.currentTimeMillis(),
        segmentationMode: String = "MEDIARECORDER_SIZE_ROTATION_GAPLESS_UNVERIFIED",
        mediaPresentationStartUs: Long? = null,
        mediaPresentationEndUs: Long? = null,
        encodedSampleCount: Long? = null
    ) {
        finalizeWorker.execute {
            val complete = File(folder, "${segment.id}.mp4")
            val metadata = File(folder, "${segment.id}.json")
            val temp = File(folder, "${segment.id}.json.tmp")
            if (complete.exists() || metadata.exists() || temp.exists()) {
                setError("Existing segment name; refusing overwrite: ${segment.id}")
                return@execute
            }
            try {
                // MediaRecorder.stop() already finalized this .recording MP4. Publish
                // its durable metadata BEFORE renaming the MP4 to avoid lost sidecars.
                if (!segment.partialFile.isFile || segment.partialFile.length() <= 0L) {
                    setError("Finalized source missing/empty: ${segment.id}")
                    return@execute
                }
                val sha = sha256(segment.partialFile)
                val data = JSONObject().apply {
                    put("schema_version", 1)
                    put("segment_id", segment.id)
                    put("device_id", Build.DEVICE)
                    put("app_version", BuildConfig.VERSION_NAME)
                    put("app_version_code", BuildConfig.VERSION_CODE)
                    put("state", "ready")
                    put("camera_id", segment.cameraId)
                    put("profile", segment.mode)
                    put("start_utc_ms_estimate", segment.startedUtcMs)
                    put("end_utc_ms_estimate", endedUtcMs)
                    put("start_monotonic_ns", segment.startedMonotonicNs)
                    put("first_exposure_sensor_ns", exposureFirstNs ?: JSONObject.NULL)
                    put("last_exposure_sensor_ns", exposureLastNs ?: JSONObject.NULL)
                    put("sensor_timestamp_source", sensorTimestampSource)
                    put("capture_result_count", captureResultCount)
                    put("timestamp_accuracy", "UNVERIFIED_FIRST_FRAME_UTC")
                    put("segmentation", segmentationMode)
                    put("segment_boundary_start", segment.boundaryStart)
                    put("segment_duration_target_ms", segment.targetSegmentMs)
                    put("segment_duration_actual_estimate_ms", (endedUtcMs - segment.startedUtcMs).coerceAtLeast(0L))
                    put("segment_boundary_verified", false)
                    put("media_presentation_start_us", mediaPresentationStartUs ?: JSONObject.NULL)
                    put("media_presentation_end_us", mediaPresentationEndUs ?: JSONObject.NULL)
                    put("encoded_sample_count", encodedSampleCount ?: JSONObject.NULL)
                    put("width", segment.width)
                    put("height", segment.height)
                    put("nominal_fps", segment.fps)
                    put("bitrate", segment.bitrate)
                    put("codec", "H264")
                    put("size_bytes", segment.partialFile.length())
                    put("sha256", sha)
                }
                FileOutputStream(temp).use { out ->
                    out.write(data.toString(2).toByteArray(Charsets.UTF_8))
                    out.fd.sync()
                }
                if (!segment.partialFile.renameTo(complete)) error("MP4 rename failed")
                if (!temp.renameTo(metadata)) error("Metadata rename failed")
                rememberCompleted(data)
                // v0.12: keep exactly ONE durable MP4 in app-private segments.
                // Hub downloads through GET /api/v1/segments/{id}/file as before.
                // No redundant MediaStore publication or automatic deletion.

            } catch (e: Exception) {
                setError("Finalization failed ${segment.id}: ${e.message}")
            }
        }
    }

    /**
     * Promote only stopped recordings that already carry complete staged metadata.
     * Unknown .recording / orphan MP4 files are NEVER guessed at, deleted, or marked READY.
     */
    private fun recoverInterruptedFinalizations() {
        folder.listFiles { file -> file.isFile && file.name.endsWith(".json.tmp") }
            ?.sortedBy { it.name }
            ?.forEach { staged ->
                val id = staged.name.removeSuffix(".json.tmp")
                if (!id.matches(Regex("[a-zA-Z0-9_-]{3,100}"))) return@forEach
                val video = File(folder, "$id.mp4")
                val stoppedRecording = File(folder, "$id.recording")
                val metadata = File(folder, "$id.json")
                try {
                    if (metadata.exists()) return@forEach  // No overwrite of completed records.
                    val sidecar = JSONObject(staged.readText(Charsets.UTF_8))
                    if (sidecar.optString("segment_id") != id ||
                        sidecar.optString("state") != "ready") error("Bad staged segment id/state")
                    val expectedSize = sidecar.optLong("size_bytes", -1)
                    val expectedSha = sidecar.optString("sha256", "")
                    if (expectedSize <= 0L || !expectedSha.matches(Regex("[0-9a-f]{64}"))) {
                        error("Invalid staged size/hash")
                    }
                    val source = when {
                        video.isFile -> video
                        stoppedRecording.isFile -> stoppedRecording
                        else -> return@forEach
                    }
                    if (source.length() != expectedSize || sha256(source) != expectedSha) {
                        error("Staged data checksum mismatch")
                    }
                    if (source == stoppedRecording && !stoppedRecording.renameTo(video)) {
                        error("Cannot promote interrupted stopped recording")
                    }
                    if (!staged.renameTo(metadata)) error("Cannot promote staged sidecar")
                    rememberCompleted(sidecar)
                } catch (e: Exception) {
                    setError("Recovery needs inspection for $id: ${e.message}")
                }
            }
    }

    /** Persist the latest READY MP4 timestamp so verified phone cleanup does not erase progress evidence. */
    private fun rememberCompleted(metadata: JSONObject) {
        val endedUtcMs = metadata.optLong("end_utc_ms_estimate", -1L)
        val id = metadata.optString("segment_id", "")
        if (endedUtcMs <= 0L || !safeSegmentId.matches(id)) return
        val prefs = context.getSharedPreferences("meteor-state", Context.MODE_PRIVATE)
        if (endedUtcMs <= prefs.getLong("recording_last_completed_utc_ms", 0L)) return
        val saved = prefs.edit()
            .putLong("recording_last_completed_utc_ms", endedUtcMs)
            .putString("recording_last_completed_segment_id", id)
            .commit()
        if (!saved) setError("READY MP4 published but last-completed timestamp could not be persisted")
    }

    private fun rememberLatestCompleted() {
        var newest: JSONObject? = null
        var newestUtcMs = 0L
        val ready = listReady()
        for (i in 0 until ready.length()) {
            val metadata = ready.optJSONObject(i) ?: continue
            val endedUtcMs = metadata.optLong("end_utc_ms_estimate", 0L)
            if (endedUtcMs > newestUtcMs) {
                newest = metadata
                newestUtcMs = endedUtcMs
            }
        }
        newest?.let(::rememberCompleted)
    }

    /** Diagnostic only: unresolved files are retained indefinitely, never purged. */
    fun unindexedFileCount(): Int {
        return folder.listFiles { f ->
            f.isFile && (f.name.endsWith(".recording") ||
                (f.name.endsWith(".mp4") && metadataFor(f.name.removeSuffix(".mp4")) == null))
        }?.size ?: 0
    }

    fun listReady(): JSONArray {
        val array = JSONArray()
        folder.listFiles { f -> f.isFile && f.name.endsWith(".json") }
            ?.sortedBy { it.name }
            ?.forEach { sidecar ->
                try {
                    val metadata = JSONObject(sidecar.readText(Charsets.UTF_8))
                    val id = metadata.getString("segment_id")
                    // Never use a field from a sidecar as a filesystem path
                    // unless it is an expected, path-safe segment identifier.
                    if (!safeSegmentId.matches(id)) return@forEach
                    val file = File(folder, "$id.mp4")
                    if (sidecar.name == "$id.json" &&
                        metadata.optString("state") == "ready" &&
                        file.isFile && file.length() == metadata.optLong("size_bytes", -1)) {
                        array.put(metadata)
                    }
                } catch (_: Exception) { /* corrupt sidecar is never made downloadable */ }
            }
        return array
    }

    fun metadataFor(id: String): JSONObject? {
        if (!safeSegmentId.matches(id)) return null
        return try {
            val sidecar = File(folder, "$id.json")
            val video = File(folder, "$id.mp4")
            if (!sidecar.isFile || !video.isFile) null
            else JSONObject(sidecar.readText(Charsets.UTF_8)).takeIf {
                it.optString("segment_id") == id && it.optString("state") == "ready" &&
                    it.optLong("size_bytes", -1) == video.length()
            }
        } catch (_: Exception) { null }
    }

    fun completedVideo(id: String): File? = if (metadataFor(id) != null) File(folder, "$id.mp4") else null

    /** Explicit remote purge of this app's own COMPLETED originals and their public copies.
     * The caller must supply the exact segment id and SHA-256 from a verified backup.
     * Never touches .recording files, unrelated apps or Android private data.
     */
    fun purgeVerified(payload: JSONObject): JSONObject {
        val items = payload.optJSONArray("segments") ?: error("segments_array_required")
        require(items.length() in 1..25) { "batch_size_1_to_25" }
        val results = JSONArray()
        var deleted = 0
        var freed = 0L
        for (i in 0 until items.length()) {
            val spec = items.getJSONObject(i)
            val id = spec.getString("segment_id")
            val sha = spec.getString("sha256")
            if (!safeSegmentId.matches(id) || !sha.matches(Regex("[0-9a-f]{64}"))) {
                results.put(JSONObject().put("segment_id", id).put("status", "invalid_id_or_sha"))
                continue
            }
            val meta = metadataFor(id)
            val file = completedVideo(id)
            if (meta == null || file == null || meta.optString("sha256") != sha || sha256(file) != sha) {
                results.put(JSONObject().put("segment_id", id).put("status", "private_file_not_verified"))
                continue
            }
            val copy = File(android.os.Environment.getExternalStorageDirectory(),
                PublicVideoExporter.PUBLIC_FOLDER + "/" + id + ".mp4")
            // Only delete the public copy when its full digest is identical to the private original.
            if (copy.exists() && (!copy.isFile || copy.length() != file.length() || sha256(copy) != sha)) {
                results.put(JSONObject().put("segment_id", id).put("status", "public_copy_not_verified"))
                continue
            }
            val hadPublicCopy = copy.isFile
            if (hadPublicCopy && !copy.delete()) {
                results.put(JSONObject().put("segment_id", id).put("status", "public_delete_failed"))
                continue
            }
            val bytes = file.length()
            if (!file.delete()) {
                results.put(JSONObject().put("segment_id", id).put("status", "private_delete_failed"))
                continue
            }
            val sidecar = File(folder, "$id.json")
            val metaDeleted = sidecar.delete()
            deleted++
            freed += bytes * (if (hadPublicCopy) 2 else 1)
            results.put(JSONObject().put("segment_id", id)
                .put("status", if (metaDeleted) "deleted" else "video_deleted_sidecar_left"))
        }
        return JSONObject().put("deleted_segments", deleted).put("approx_bytes_freed", freed)
            .put("results", results).put("automatic_delete_enabled", false)
    }

    fun freeBytes(): Long = folder.usableSpace

    private fun setError(value: String) {
        context.getSharedPreferences("meteor-state", Context.MODE_PRIVATE)
            .edit().putString("last_error", value).apply()
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { input ->
            val buf = ByteArray(1024 * 1024)
            while (true) {
                val count = input.read(buf)
                if (count < 0) break
                digest.update(buf, 0, count)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
