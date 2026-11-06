package org.meteormesh.node

import android.content.Context
import android.os.Build
import java.net.InetAddress
import java.security.MessageDigest
import java.util.Locale

/** Single user-entered PIN 0000; derived per-camera tokens keep existing Hub protocol unchanged. */
data class HubConfig(val ip: String, val fingerprint: String, val token: String)

object HubControlConfig {
    private const val P = "meteor-hub-control"
    private const val SHARED_PIN = "0000"
    // SHA-256 of the installed HP G62 HTTPS certificate. Public fingerprint, not a password.
    private const val CERT_SHA256 = "aa2f35e659a795bbd50ba8fade8de916ae7308c5cef3247a3fba74683c282c7b"
    private fun perCameraToken(device: String): String = MessageDigest.getInstance("SHA-256")
        .digest("meteormesh.control013.pin.v1:$device:$SHARED_PIN".toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it.toInt() and 255) }

    fun deviceId(): String? = when (Build.DEVICE.lowercase(Locale.ROOT)) {
        "viva" -> "cam-viva"
        "ruby" -> "cam-ruby"
        "surya" -> "cam-surya"
        else -> null
    }
    fun validIp(ip: String): Boolean {
        val octets = ip.split('.')
        if (octets.size != 4 || octets.any { it.isEmpty() || it.length > 3 || it.any { c -> !c.isDigit() } }) return false
        val n = octets.map { it.toIntOrNull() ?: return false }
        if (n.any { it !in 0..255 }) return false
        val a = InetAddress.getByAddress(n.map { it.toByte() }.toByteArray())
        return a.isSiteLocalAddress
    }
    fun save(context: Context, ip: String, pin: String): Boolean {
        val device = deviceId() ?: return false
        if (!validIp(ip.trim()) || pin.trim() != SHARED_PIN) return false
        return context.getSharedPreferences(P, Context.MODE_PRIVATE).edit()
            .putString("host_ip", ip.trim())
            .putString("cert_sha256", CERT_SHA256)
            .putString("device_token", perCameraToken(device))
            .remove("processed_command_id")
            .commit()
    }
    fun clear(context: Context) = context.getSharedPreferences(P, Context.MODE_PRIVATE).edit()
        .remove("host_ip").remove("cert_sha256").remove("device_token")
        .remove("processed_command_id").commit()
    fun load(context: Context): HubConfig? {
        if (deviceId() == null) return null
        val p = context.getSharedPreferences(P, Context.MODE_PRIVATE)
        val ip=p.getString("host_ip", "") ?: ""
        val sha=p.getString("cert_sha256", "") ?: ""
        val token=p.getString("device_token", "") ?: ""
        if (!validIp(ip) || sha != CERT_SHA256 || token != perCameraToken(deviceId()!!)) return null
        return HubConfig(ip,sha,token)
    }
    fun lastAck(context: Context): String? = context.getSharedPreferences(P, Context.MODE_PRIVATE)
        .getString("processed_command_id", null)
    fun saveAck(context: Context, commandId: String) {
        context.getSharedPreferences(P, Context.MODE_PRIVATE).edit()
            .putString("processed_command_id",commandId).apply()
    }
    fun note(context: Context, status: String) {
        context.getSharedPreferences(P, Context.MODE_PRIVATE).edit()
            .putString("last_status",status.take(100))
            .putLong("last_status_time_ms",System.currentTimeMillis()).apply()
    }
}
