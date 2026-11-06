package org.meteormesh.node

import android.content.Context
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import android.util.Log
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.File
import java.io.OutputStream
import java.io.RandomAccessFile
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.concurrent.Executors

/**
 * MeteorNode v0.11 Remote Shared Storage LAN TEST: deliberately plain HTTP, NO TLS or Android Keystore.
 * The service only accepts loopback / RFC1918 peers. GET /health has no secret;
 * all file and control routes require the fixed test Bearer key (non-secret).
 * Storage endpoints require a separate random admin key and Android All Files Access.
 * Never port-forward port 8080 to the Internet.
 */
class LocalHttpServer(private val context: Context, private val segments: SegmentStore) {
    companion object {
        const val PORT = 8080
        /** Experimental shared key, intentionally embedded at user request.
         *  Not a secret: anyone who can see the APK can obtain it.
         *  NEVER allow this build beyond an isolated trusted LAN.
         */
        const val FIXED_TEST_TOKEN = "00000000000000000000000000000000"
        fun pairingToken(@Suppress("UNUSED_PARAMETER") context: Context): String = FIXED_TEST_TOKEN

    }

    @Volatile private var running = false
    @Volatile private var listener: ServerSocket? = null
    private val workers = Executors.newFixedThreadPool(3) { runnable ->
        Thread(runnable, "meteor-http-client").apply { isDaemon = true }
    }

    private fun state(text: String, error: String = "") {
        context.getSharedPreferences("meteor-lan", Context.MODE_PRIVATE).edit()
            .putString("server_status", text).putString("last_http_error", error).apply()
    }

    fun start() {
        if (running) return
        running = true
        state("starting")
        Thread({
            try {
                pairingToken(context)
                ServerSocket(PORT).use { server ->
                    listener = server
                    state("listening")
                    while (running) {
                        val client = try { server.accept() } catch (_: SocketException) { break }
                        try {
                            workers.execute { client.use { handle(it) } }
                        } catch (e: Exception) {
                            client.close()
                            Log.w("MeteorNodeHTTP", "HTTP queue failure", e)
                        }
                    }
                }
            } catch (e: Exception) {
                val message = "${e.javaClass.simpleName}: ${e.message ?: "unknown"}"
                state("error", message)
                Log.e("MeteorNodeHTTP", "HTTP listener failed", e)
            } finally {
                listener = null
                running = false
            }
        }, "meteor-http-listener").apply { isDaemon = true }.start()
    }

    /** An FGS can remain alive after a bind/accept failure. Expose listener liveness.
     * This is not an end-to-end HTTP availability guarantee; HP verifies separately.
     */
    fun isListening(): Boolean = running && listener?.let { it.isBound && !it.isClosed } == true

    fun stop() {
        running = false
        try { listener?.close() } catch (_: Exception) { }
        workers.shutdownNow()
        state("stopped")
    }

    private fun handle(socket: Socket) {
        try {
            val peer = socket.inetAddress
            if (!peer.isLoopbackAddress && !peer.isSiteLocalAddress) {
                Log.w("MeteorNodeHTTP", "Rejected non-LAN connection")
                return
            }
            socket.soTimeout = 12_000
            val input = BufferedInputStream(socket.getInputStream())
            val output = BufferedOutputStream(socket.getOutputStream())
            val first = readAsciiLine(input, 4096) ?: return
            val request = first.split(' ')
            val method = request.getOrNull(0) ?: ""
            if (request.size < 3 || method !in setOf("GET", "POST")) {
                sendText(output, 405, "Method Not Allowed", "text/plain", "GET or POST only")
                return
            }
            val path = request[1].substringBefore('?')
            var authorization: String? = null
            var range: String? = null
            var contentLength: Int? = null
            var contentType: String? = null
            var storageKey: String? = null
            var count = first.length
            while (true) {
                val line = readAsciiLine(input, 4096) ?: return
                count += line.length
                if (count > 16_384) return
                if (line.isEmpty()) break
                when (line.substringBefore(':').lowercase()) {
                    "authorization" -> authorization = line.substringAfter(':').trim()
                    "range" -> range = line.substringAfter(':').trim()
                    "content-length" -> contentLength = line.substringAfter(':').trim().toIntOrNull()
                    "content-type" -> contentType = line.substringAfter(':').trim()
                    "x-meteor-storage-key" -> storageKey = line.substringAfter(':').trim()
                }
            }

            // Diagnostic endpoint is intentionally open on the trusted LAN.
            if (method == "GET" && path == "/api/v1/health") {
                sendText(output, 200, "OK", "application/json", health().toString())
                return
            }
            val expected = "Bearer ${pairingToken(context)}".toByteArray(StandardCharsets.UTF_8)
            val provided = authorization?.toByteArray(StandardCharsets.UTF_8)
            if (provided == null || !MessageDigest.isEqual(provided, expected)) {
                sendText(output, 401, "Unauthorized", "application/json", "{\"error\":\"pairing_required\"}")
                return
            }
            // Authorized shared storage management. Separate random key from recording token.
            if (path.startsWith("/api/v1/storage/")) {
                val supplied = storageKey?.toByteArray(StandardCharsets.UTF_8)
                val key = RemoteStorage.adminKey(context).toByteArray(StandardCharsets.UTF_8)
                if (supplied == null || !MessageDigest.isEqual(supplied, key)) {
                    sendText(output, 403, "Forbidden", "application/json", "{\"error\":\"storage_admin_key_required\"}")
                    return
                }
                if (!RemoteSettings.enabled(context) || !RemoteStorage.granted()) {
                    sendText(output, 403, "Forbidden", "application/json", "{\"error\":\"remote_or_storage_permission_disabled\"}")
                    return
                }
                try {
                    fun parameter(key: String): String {
                        val query = request[1].substringAfter('?', "")
                        val raw = query.split('&').firstOrNull { it.substringBefore('=') == key }
                            ?.substringAfter('=', "") ?: ""
                        return java.net.URLDecoder.decode(raw, "UTF-8")
                    }
                    when {
                        method == "GET" && path == "/api/v1/storage/list" ->
                            sendText(output, 200, "OK", "application/json", RemoteStorage.list(parameter("dir")).toString())
                        method == "GET" && path == "/api/v1/storage/file" ->
                            sendFile(output, RemoteStorage.fileForRead(parameter("path")), range, "application/octet-stream")
                        method == "POST" && path == "/api/v1/storage/upload" -> {
                            val n = contentLength ?: -1
                            val result = RemoteStorage.upload(parameter("path"), parameter("overwrite") == "1", n, input)
                            sendText(output, 200, "OK", "application/json", result.toString())
                        }
                        method == "POST" && path in setOf(
                            "/api/v1/storage/mkdir", "/api/v1/storage/move",
                            "/api/v1/storage/delete", "/api/v1/storage/delete-tree",
                            "/api/v1/storage/purge-recordings"
                        ) -> {
                            val n = contentLength ?: -1
                            if (n !in 2..32768 || contentType?.substringBefore(';')?.trim() != "application/json") {
                                sendText(output, 400, "Bad Request", "application/json", "{\"error\":\"invalid_json_body\"}")
                                return
                            }
                            val raw = ByteArray(n)
                            var read = 0
                            while (read < n) {
                                val got = input.read(raw, read, n-read)
                                if (got <= 0) return
                                read += got
                            }
                            val payload = JSONObject(String(raw, StandardCharsets.UTF_8))
                            val result = when (path) {
                                "/api/v1/storage/mkdir" -> RemoteStorage.mkdir(payload)
                                "/api/v1/storage/move" -> RemoteStorage.rename(payload)
                                "/api/v1/storage/delete" -> RemoteStorage.deleteBatch(payload)
                                "/api/v1/storage/delete-tree" -> RemoteStorage.deleteTree(payload)
                                else -> segments.purgeVerified(payload)
                            }
                            sendText(output, 200, "OK", "application/json", result.toString())
                        }
                        else -> sendText(output, 404, "Not Found", "application/json", "{\"error\":\"unknown_storage_operation\"}")
                    }
                } catch (e: Exception) {
                    sendText(output, 400, "Bad Request", "application/json", JSONObject()
                        .put("error", e.message ?: "storage_error").toString())
                }
                return
            }
            if (method == "POST") {
                if (!RemoteSettings.enabled(context)) {
                    sendText(output, 403, "Forbidden", "application/json", "{\"error\":\"remote_control_disabled_on_phone\"}")
                    return
                }
                // Strictly bounded request body, no chunked requests and no arbitrary commands.
                val n = contentLength
                if (n == null || n < 0 || n > 4096) {
                    sendText(output, 400, "Bad Request", "application/json", "{\"error\":\"invalid_content_length\"}")
                    return
                }
                val body = ByteArray(n)
                var read = 0
                while (read < n) {
                    val part = input.read(body, read, n - read)
                    if (part <= 0) return
                    read += part
                }
                val payload = String(body, StandardCharsets.UTF_8)
                when (path) {
                    "/api/v1/settings" -> {
                        if (contentType?.substringBefore(';')?.trim()?.lowercase() != "application/json") {
                            sendText(output, 415, "Unsupported Media Type", "application/json", "{\"error\":\"json_required\"}")
                            return
                        }
                        try {
                            val result = RemoteSettings.update(context, JSONObject(payload))
                            RemoteSettings.noteCommand(context, "settings_changed")
                            sendText(output, 200, "OK", "application/json", result.toString())
                        } catch (e: Exception) {
                            sendText(output, 400, "Bad Request", "application/json", JSONObject()
                                .put("error", "invalid_settings").put("detail", e.message ?: "invalid JSON").toString())
                        }
                    }
                    "/api/v1/control/start" -> {
                        if (n != 0) {
                            sendText(output, 400, "Bad Request", "application/json", "{\"error\":\"body_not_allowed\"}")
                        } else if (MeteorCaptureService.remoteStart(context)) {
                            sendText(output, 202, "Accepted", "application/json", "{\"status\":\"queued_check_health\"}")
                        } else {
                            sendText(output, 409, "Conflict", "application/json", "{\"error\":\"start_camera_locally_once_first\"}")
                        }
                    }
                    "/api/v1/control/stop" -> {
                        if (n != 0) {
                            sendText(output, 400, "Bad Request", "application/json", "{\"error\":\"body_not_allowed\"}")
                        } else if (MeteorCaptureService.remoteStop(context)) {
                            sendText(output, 202, "Accepted", "application/json", "{\"status\":\"queued_check_health\"}")
                        } else {
                            sendText(output, 409, "Conflict", "application/json", "{\"error\":\"camera_service_not_running\"}")
                        }
                    }
                    else -> sendText(output, 404, "Not Found", "application/json", "{\"error\":\"not_found\"}")
                }
                return
            }
            when {
                path == "/api/v1/status" -> sendText(
                    output, 200, "OK", "application/json", health().toString()
                )
                path == "/api/v1/permissions" -> sendText(
                    output, 200, "OK", "application/json", PermissionStatus.snapshot(context).toString()
                )
                path == "/api/v1/settings" -> sendText(
                    output, 200, "OK", "application/json", RemoteSettings.current(context).toString()
                )
                path == "/api/v1/capabilities" -> sendText(
                    output, 200, "OK", "application/json", CameraInventory.json(context).toString()
                )
                path == "/api/v1/segments" -> sendText(
                    output, 200, "OK", "application/json", JSONObject()
                        .put("segments", segments.listReady()).toString()
                )
                path == "/api/v1/retention" -> sendText(
                    output, 200, "OK", "application/json", "{\"mode\":\"dry-run\",\"auto_delete\":false}"
                )
                path.startsWith("/api/v1/segments/") && path.endsWith("/meta") -> {
                    val id = path.removePrefix("/api/v1/segments/").removeSuffix("/meta")
                    val metadata = segments.metadataFor(id)
                    if (metadata == null) sendText(output, 404, "Not Found", "text/plain", "not found")
                    else sendText(output, 200, "OK", "application/json", metadata.toString())
                }
                path.startsWith("/api/v1/segments/") && path.endsWith("/file") -> {
                    val id = path.removePrefix("/api/v1/segments/").removeSuffix("/file")
                    val file = segments.completedVideo(id)
                    if (file == null) sendText(output, 404, "Not Found", "text/plain", "not found")
                    else sendFile(output, file, range)
                }
                else -> sendText(output, 404, "Not Found", "text/plain", "not found")
            }
        } catch (e: Exception) {
            // Socket disconnect must never interrupt a camera recording, but it must be diagnosable.
            val message = "${e.javaClass.simpleName}: ${e.message ?: "peer disconnected"}"
            context.getSharedPreferences("meteor-lan", Context.MODE_PRIVATE).edit()
                .putString("last_http_error", message).apply()
            Log.w("MeteorNodeHTTP", "HTTP request failed", e)
        }
    }

    private fun health(): JSONObject {
        val prefs = context.getSharedPreferences("meteor-state", Context.MODE_PRIVATE)
        val lanPrefs = context.getSharedPreferences("meteor-lan", Context.MODE_PRIVATE)
        val readySegments = segments.listReady()
        val lastCompletedUtcMs = prefs.getLong("recording_last_completed_utc_ms", 0L)
        val battery = context.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        val power = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        return JSONObject().apply {
            put("api_version", "v1-alpha")
            // Legacy Hub 0.9 and capture scheduler recognize only Node 0.10 protocol.
            // App version is separately exposed; storage functionality is v0.11.
            put("node_version", "0.10.12-v013-storage-compatible")
            put("app_version", BuildConfig.VERSION_NAME)
            put("storage_admin_available", RemoteStorage.granted())
            put("transport", "HTTP_LAN_TEST_NO_TLS")
            put("device_id", Build.DEVICE)
            put("state", prefs.getString("state", "idle"))
            put("last_error", prefs.getString("last_error", ""))
            put("battery_percent", battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY))
            put("thermal_status", if (Build.VERSION.SDK_INT >= 29) power.currentThermalStatus else JSONObject.NULL)
            put("free_bytes", segments.freeBytes())
            put("protected_storage_reserve_bytes", RecordingPolicy.PROTECTED_RESERVE_BYTES)
            put("protected_storage_reserve_ok", segments.freeBytes() >= RecordingPolicy.PROTECTED_RESERVE_BYTES)
            put("recording_rotation_mode", "MEDIACODEC_PTS_TIMED_OR_EMERGENCY_SIZE_SYNC_FRAME")
            put("recording_rotation_state", prefs.getString("recording_rotation_state", "idle"))
            put("recording_active_segment_id", prefs.getString("recording_active_segment_id", ""))
            put("recording_next_segment_id", prefs.getString("recording_next_segment_id", ""))
            put("recording_segment_target_ms", prefs.getLong("recording_segment_target_ms", 0L))
            put("recording_segment_file_limit_bytes", prefs.getLong("recording_segment_file_limit_bytes", 0L))
            put("recording_segment_expected_bytes", prefs.getLong("recording_segment_expected_bytes", 0L))
            put("recording_segment_emergency_limit_bytes", prefs.getLong("recording_segment_emergency_limit_bytes", 0L))
            put("recording_last_switch_utc_ms", prefs.getLong("recording_last_switch_utc_ms", 0L))
            put("recording_segment_boundary_verified", false)
            put("segments_ready", readySegments.length())
            put("ready_segments", readySegments.length())
            put("recording_last_completed_utc_ms", if (lastCompletedUtcMs > 0L) lastCompletedUtcMs else JSONObject.NULL)
            put("recording_last_completed_segment_id", prefs.getString("recording_last_completed_segment_id", ""))
            put("recording_last_completed_time_basis", "SIDECAR_END_UTC_ESTIMATE_AFTER_READY_PUBLISH")
            put("public_folder", PublicVideoExporter.PUBLIC_FOLDER)
            put("public_export_enabled", false)
            put("video_storage_mode", "APP_PRIVATE_SINGLE_COPY")
            put("remote_control_enabled", RemoteSettings.enabled(context))
            put("remote_camera_ready", MeteorCaptureService.readyForRemote(context))
            put("permissions", PermissionStatus.snapshot(context))
            put("lan_status", context.getSharedPreferences("meteor-lan", Context.MODE_PRIVATE)
                .getString("server_status", "not-started"))
            put("lan_cpu_wake_lock_held", lanPrefs.getBoolean("cpu_wake_lock_held", false))
            put("lan_wifi_lock_held", lanPrefs.getBoolean("wifi_lock_held", false))
            put("lan_wifi_lock_mode", lanPrefs.getString("wifi_lock_mode", "not-held"))
            put("lan_connectivity_lock_error", lanPrefs.getString("connectivity_lock_error", ""))
            put("battery_optimization_exempt", power.isIgnoringBatteryOptimizations(context.packageName))
            put("device_idle_mode", power.isDeviceIdleMode)
            put("lan_recovery_attempts", context.getSharedPreferences("meteor-lan", Context.MODE_PRIVATE)
                .getInt("recovery_attempts", 0))
            put("lan_recovery_last_ms", context.getSharedPreferences("meteor-lan", Context.MODE_PRIVATE)
                .getLong("recovery_last_ms", 0L))
            put("last_remote_command", RemoteSettings.lastCommand(context))
            put("retention_auto_delete", false)
        }
    }

    private fun sendText(out: OutputStream, code: Int, reason: String, contentType: String, body: String) {
        val bytes = body.toByteArray(StandardCharsets.UTF_8)
        val headers = "HTTP/1.1 $code $reason\r\n" +
            "Content-Type: $contentType; charset=utf-8\r\n" +
            "Content-Length: ${bytes.size}\r\n" +
            "Cache-Control: no-store\r\n" +
            "Connection: close\r\n\r\n"
        out.write(headers.toByteArray(StandardCharsets.US_ASCII))
        out.write(bytes)
        out.flush()
    }

    private fun sendFile(out: OutputStream, file: File, range: String?, contentType: String = "video/mp4") {
        val length = file.length()
        if (length <= 0) {
            sendText(out, 404, "Not Found", "text/plain", "empty file")
            return
        }
        val startEnd: Pair<Long, Long>? = if (range == null) 0L to (length - 1) else parseRange(range, length)
        if (startEnd == null) {
            val headers = "HTTP/1.1 416 Range Not Satisfiable\r\nContent-Range: bytes */$length\r\n" +
                "Content-Length: 0\r\nConnection: close\r\n\r\n"
            out.write(headers.toByteArray(StandardCharsets.US_ASCII))
            out.flush()
            return
        }
        val (start, end) = startEnd
        val count = end - start + 1
        val partial = range != null
        val headers = "HTTP/1.1 ${if (partial) "206 Partial Content" else "200 OK"}\r\n" +
            "Content-Type: $contentType\r\n" +
            "Content-Length: $count\r\n" +
            "Accept-Ranges: bytes\r\n" +
            (if (partial) "Content-Range: bytes $start-$end/$length\r\n" else "") +
            "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
        out.write(headers.toByteArray(StandardCharsets.US_ASCII))
        RandomAccessFile(file, "r").use { raf ->
            raf.seek(start)
            val buf = ByteArray(64 * 1024)
            var remaining = count
            while (remaining > 0) {
                val n = raf.read(buf, 0, minOf(buf.size.toLong(), remaining).toInt())
                if (n <= 0) break
                out.write(buf, 0, n)
                remaining -= n
            }
        }
        out.flush()
    }

    private fun parseRange(value: String, size: Long): Pair<Long, Long>? {
        if (!value.startsWith("bytes=") || value.contains(',')) return null
        val bits = value.removePrefix("bytes=").split('-', limit = 2)
        if (bits.size != 2) return null
        return try {
            if (bits[0].isBlank()) {
                val suffix = bits[1].toLong()
                if (suffix <= 0) null else maxOf(0, size - suffix) to (size - 1)
            } else {
                val start = bits[0].toLong()
                val end = if (bits[1].isBlank()) size - 1 else minOf(size - 1, bits[1].toLong())
                if (start < 0 || start >= size || end < start) null else start to end
            }
        } catch (_: NumberFormatException) { null }
    }

    private fun readAsciiLine(input: BufferedInputStream, maxChars: Int): String? {
        val bytes = ArrayList<Byte>()
        while (bytes.size <= maxChars) {
            val next = input.read()
            if (next < 0) return null
            if (next == '\n'.code) {
                if (bytes.isNotEmpty() && bytes.last() == '\r'.code.toByte()) bytes.removeAt(bytes.size - 1)
                return String(bytes.toByteArray(), StandardCharsets.US_ASCII)
            }
            bytes.add(next.toByte())
        }
        return null
    }
}
