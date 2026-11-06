package org.meteormesh.node

import android.content.Context
import android.os.Build
import android.os.Environment
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.InputStream
import java.io.IOException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.security.SecureRandom

/** Shared storage management only: no shell, no other apps' private data, no root.
 * Android All Files Access is explicitly granted once from system settings.
 * Every remote operation thereafter is authorized by a separate random admin key.
 */
object RemoteStorage {
    private fun root() = Environment.getExternalStorageDirectory().canonicalFile
    fun granted(): Boolean = Build.VERSION.SDK_INT < 30 || Environment.isExternalStorageManager()

    fun adminKey(context: Context): String {
        val prefs = context.getSharedPreferences("meteor-storage-admin", Context.MODE_PRIVATE)
        val old = prefs.getString("storage_key", null)
        if (old != null && old.matches(Regex("[0-9a-f]{64}"))) return old
        val bytes = ByteArray(32)
        SecureRandom().nextBytes(bytes)
        val secret = bytes.joinToString("") { "%02x".format(it.toInt() and 255) }
        if (!prefs.edit().putString("storage_key", secret).commit()) throw IOException("key_save_failed")
        return secret
    }

    private fun pathOf(path: String, rootAllowed: Boolean = false): File {
        if (!granted()) throw SecurityException("all_files_permission_required")
        if (path.length > 1024 || path.startsWith('/') || path.contains('\\') ||
            path.contains('\u0000') || path.contains(':'))
            throw IllegalArgumentException("invalid_path")
        if (path.isEmpty()) {
            if (rootAllowed) return root()
            throw IllegalArgumentException("root_operation_forbidden")
        }
        val parts = path.split('/')
        if (parts.any { it.isEmpty() || it == "." || it == ".." })
            throw IllegalArgumentException("protected_or_invalid_path")
        // Android/media is shared storage, while Android/data and Android/obb are protected.
        if (parts.first().equals("Android", ignoreCase = true) &&
            (parts.size < 2 || !parts[1].equals("media", ignoreCase = true)))
            throw IllegalArgumentException("protected_android_directory")
        val base = root()
        val target = File(base, path)
        val canonical = target.canonicalFile
        if (target.absolutePath != canonical.path || !canonical.path.startsWith(base.path + File.separator))
            throw IllegalArgumentException("path_escape_or_symlink")
        return canonical
    }

    private fun entry(file: File): JSONObject = JSONObject()
        .put("path", file.relativeTo(root()).invariantSeparatorsPath)
        .put("directory", file.isDirectory)
        .put("bytes", if (file.isFile) file.length() else 0L)
        .put("modified_ms", file.lastModified())

    fun list(relativeDirectory: String): JSONObject {
        val folder = pathOf(relativeDirectory, rootAllowed = true)
        if (!folder.isDirectory) throw IllegalArgumentException("not_a_directory")
        val all = folder.listFiles() ?: throw IOException("list_failed")
        val filtered = all.filter { file ->
            if (folder == root() && file.name.equals("Android", ignoreCase = true)) return@filter false
            try { file.absolutePath == file.canonicalPath && file.canonicalPath.startsWith(root().path + "/") }
            catch (_: IOException) { false }
        }.sortedWith(compareBy<File> { !it.isDirectory }.thenBy { it.name })
        val items = JSONArray()
        for (file in filtered.take(1500)) items.put(entry(file))
        return JSONObject().put("directory", relativeDirectory).put("entries", items)
            .put("truncated", filtered.size > 1500).put("all_files_access", true)
    }

    fun fileForRead(relative: String): File {
        val file = pathOf(relative)
        if (!file.isFile || !file.canRead()) throw IllegalArgumentException("file_not_readable")
        return file
    }

    fun mkdir(payload: JSONObject): JSONObject {
        val file = pathOf(payload.getString("path"))
        if (file.exists()) throw IllegalArgumentException("target_exists")
        if (file.parentFile?.isDirectory != true) throw IllegalArgumentException("parent_missing")
        if (!file.mkdir()) throw IOException("mkdir_failed")
        return JSONObject().put("created", entry(file))
    }

    fun rename(payload: JSONObject): JSONObject {
        val source = pathOf(payload.getString("from"))
        val destination = pathOf(payload.getString("to"))
        if (!source.exists() || destination.exists() || destination.parentFile?.isDirectory != true)
            throw IllegalArgumentException("missing_source_or_destination_busy")
        if (source.isDirectory && destination.canonicalPath.startsWith(source.canonicalPath + "/"))
            throw IllegalArgumentException("destination_inside_source")
        if (!source.renameTo(destination)) throw IOException("move_failed")
        return JSONObject().put("moved", entry(destination))
    }

    /** Exact metadata must match before deleting; no path wildcard and no implicit periodic deletion. */
    fun deleteBatch(payload: JSONObject): JSONObject {
        val items = payload.optJSONArray("files") ?: throw IllegalArgumentException("files_array_required")
        if (items.length() !in 1..50) throw IllegalArgumentException("batch_size_1_to_50")
        val result = JSONArray()
        var deleted = 0
        var freed = 0L
        for (i in 0 until items.length()) {
            val item = items.getJSONObject(i)
            val relative = item.getString("path")
            val file = pathOf(relative)
            val bytes = item.getLong("bytes")
            val timestamp = item.getLong("modified_ms")
            val sha = item.optString("sha256", "")
            var status = "missing_or_changed"
            if (file.isFile && bytes >= 0 && timestamp > 0 && file.length() == bytes && file.lastModified() == timestamp) {
                if (sha.isEmpty() || (sha.matches(Regex("[0-9a-f]{64}")) && sha == sha256(file))) {
                    status = if (file.delete()) "deleted" else "delete_failed"
                    if (status == "deleted") { deleted++; freed += bytes }
                } else status = "sha256_mismatch"
            }
            result.put(JSONObject().put("path", relative).put("status", status))
        }
        return JSONObject().put("deleted", deleted).put("freed_bytes", freed)
            .put("results", result).put("automatic_delete_enabled", false)
    }

    /** Directory deletion requires an explicit recursive flag and exact directory timestamp. */
    fun deleteTree(payload: JSONObject): JSONObject {
        val path = payload.getString("path")
        val dir = pathOf(path)
        if (!payload.optBoolean("recursive", false)) throw IllegalArgumentException("recursive_flag_required")
        if (!dir.isDirectory || dir.lastModified() != payload.getLong("modified_ms"))
            throw IllegalArgumentException("directory_missing_or_changed")
        var count = 0
        var bytes = 0L
        fun sweep(file: File, depth: Int) {
            if (depth > 32 || count >= 20000) throw IOException("tree_limit_reached")
            pathOf(file.relativeTo(root()).invariantSeparatorsPath)
            if (file.isDirectory) {
                val children = file.listFiles() ?: throw IOException("cannot_list_directory")
                for (child in children) sweep(child, depth + 1)
            } else { bytes += file.length() }
            if (!file.delete()) throw IOException("delete_failed:${file.name}")
            count++
        }
        sweep(dir, 0)
        return JSONObject().put("deleted_entries", count).put("deleted_bytes", bytes)
    }

    /** Stream upload to a sibling temporary file; publish only when every byte is received.
     * No path traversal, symlink following or write to app data. Max 512 MiB per request.
     */
    fun upload(relative: String, overwrite: Boolean, size: Int, data: InputStream): JSONObject {
        if (size !in 1..(512 * 1024 * 1024)) throw IllegalArgumentException("upload_size_out_of_range")
        val target = pathOf(relative)
        val parent = target.parentFile ?: throw IllegalArgumentException("no_parent")
        if (!parent.isDirectory || (target.exists() && (!target.isFile || !overwrite)))
            throw IllegalArgumentException("cannot_overwrite_target")
        if (Environment.getExternalStorageDirectory().usableSpace < size.toLong() + 32L * 1024 * 1024)
            throw IOException("insufficient_storage")
        val tmp = File.createTempFile(".meteorupload-", ".part", parent)
        try {
            val digest = MessageDigest.getInstance("SHA-256")
            FileOutputStream(tmp).use { out ->
                val buffer = ByteArray(65536)
                var remaining = size
                while (remaining > 0) {
                    val got = data.read(buffer, 0, minOf(buffer.size, remaining))
                    if (got <= 0) throw IOException("upload_interrupted")
                    out.write(buffer, 0, got)
                    digest.update(buffer, 0, got)
                    remaining -= got
                }
                out.fd.sync()
            }
            // Revalidate target and parent after upload; prohibit symlink swaps.
            pathOf(relative)
            if (parent.absolutePath != parent.canonicalPath) throw IOException("parent_changed")
            if (overwrite) Files.move(tmp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING)
            else Files.move(tmp.toPath(), target.toPath())
            val hex = digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
            return JSONObject().put("saved", entry(target)).put("sha256", hex)
        } finally { tmp.delete() }
    }

    private fun sha256(file: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { input ->
            val buf = ByteArray(65536)
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                if (n > 0) md.update(buf, 0, n)
            }
        }
        return md.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
    }
}
