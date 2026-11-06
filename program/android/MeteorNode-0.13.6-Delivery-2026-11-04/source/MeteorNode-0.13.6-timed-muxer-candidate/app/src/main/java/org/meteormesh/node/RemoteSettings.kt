package org.meteormesh.node

import android.content.Context
import org.json.JSONObject

/** User-controlled, allowlisted configuration; never exposes Android system settings. */
object RemoteSettings {
    private const val PREFS = "meteor-remote"
    val PROFILES = setOf("auto", "manual_800", "manual_1600")
    val BITRATES = setOf(2, 4, 6, 8, 12)
    val SEGMENT_SECONDS = setOf(60, 120, 300, 600, 900)

    fun enabled(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("enabled", false)

    fun setEnabled(context: Context, enabled: Boolean): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putBoolean("enabled", enabled).commit()

    fun current(context: Context): JSONObject {
        val p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return JSONObject().apply {
            put("remote_control_enabled", p.getBoolean("enabled", false))
            put("profile", p.getString("profile", "auto"))
            put("bitrate_mbps", p.getInt("bitrate_mbps", 6))
            put("segment_seconds", p.getInt("segment_seconds", 300))
            put("video_directory", "app_private_segments")
            put("auto_delete", false)
            put("apply_policy", "NEXT_RECORDING_START")
            put("segment_boundary_mode", "MEDIACODEC_PTS_TIMED_OR_EMERGENCY_SIZE_SYNC_FRAME")
            put("segment_seconds_semantics", "PRESENTATION_TIMESTAMP_TARGET_KEYFRAME_ALIGNED_WITH_SIZE_CAP")
            put("gapless_boundary_verified", false)
            put("allowed_profiles", org.json.JSONArray(PROFILES.sorted()))
            put("allowed_bitrates_mbps", org.json.JSONArray(BITRATES.sorted()))
            put("allowed_segment_seconds", org.json.JSONArray(SEGMENT_SECONDS.sorted()))
        }
    }

    /** Atomic validation: any unknown field or invalid value rejects the entire update. */
    @Synchronized fun update(context: Context, body: JSONObject): JSONObject {
        require(body.length() > 0) { "settings body must not be empty" }
        require(body.length() <= 3) { "too many settings" }
        val p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val e = p.edit()
        val keys = body.keys()
        while (keys.hasNext()) {
            when (val key = keys.next()) {
                "profile" -> {
                    val value = body.optString(key, "")
                    require(value in PROFILES) { "invalid profile" }
                    e.putString(key, value)
                }
                "bitrate_mbps" -> {
                    val value = body.optInt(key, -1)
                    require(value in BITRATES) { "invalid bitrate_mbps" }
                    e.putInt(key, value)
                }
                "segment_seconds" -> {
                    val value = body.optInt(key, -1)
                    require(value in SEGMENT_SECONDS) { "invalid segment_seconds" }
                    e.putInt(key, value)
                }
                else -> throw IllegalArgumentException("unsupported setting: $key")
            }
        }
        check(e.commit()) { "could not persist settings" }
        return current(context)
    }

    fun profile(context: Context) = current(context).getString("profile")
    fun bitrate(context: Context) = current(context).getInt("bitrate_mbps") * 1_000_000
    fun segmentMs(context: Context) = current(context).getInt("segment_seconds") * 1_000L

    fun noteCommand(context: Context, value: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString("last_command", value).apply()
    }
    fun lastCommand(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        .getString("last_command", "none") ?: "none"
}
