package org.meteormesh.node

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import java.io.File
import java.io.FileInputStream

/**
 * Publishes a COPY of each completely finalized MP4 in the visible Movies folder.
 * Keeps the original private MP4+JSON+SHA, and never removes an original file.
 * Android < 10 is deliberately excluded because it needs legacy storage permissions.
 */
object PublicVideoExporter {
    const val PUBLIC_FOLDER = "Movies/MeteorMesh_RemoteTest"

    fun publish(context: Context, source: File): String {
        if (Build.VERSION.SDK_INT < 29) return "Android 10+ required for shared Movies export"
        check(source.isFile && source.length() > 0L) { "Source MP4 missing" }
        // The duplicate public copy is intentionally disabled when available space is low.
        check(source.usableSpace > source.length() + 2L * 1024 * 1024 * 1024) {
            "Not enough free space for public copy"
        }
        val resolver = context.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Video.Media.DISPLAY_NAME, source.name)
            put(MediaStore.Video.Media.MIME_TYPE, "video/mp4")
            put(MediaStore.Video.Media.RELATIVE_PATH, "${Environment.DIRECTORY_MOVIES}/MeteorMesh_RemoteTest")
            put(MediaStore.Video.Media.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values)
            ?: error("MediaStore insert failed")
        try {
            val output = resolver.openOutputStream(uri, "w") ?: error("MediaStore stream failed")
            output.use { dest -> FileInputStream(source).use { it.copyTo(dest, 256 * 1024) } }
            val completed = ContentValues().apply { put(MediaStore.Video.Media.IS_PENDING, 0) }
            check(resolver.update(uri, completed, null, null) == 1) { "MediaStore publish failed" }
            context.getSharedPreferences("meteor-public-export", Context.MODE_PRIVATE).edit()
                .putString("last_public_mp4", source.name)
                .putLong("last_public_bytes", source.length())
                .putString("last_public_error", "")
                .apply()
            return uri.toString()
        } catch (e: Exception) {
            // Remove only the incomplete, newly created MediaStore copy; never source MP4.
            try { resolver.delete(uri, null, null) } catch (_: Exception) { }
            throw e
        }
    }
}
