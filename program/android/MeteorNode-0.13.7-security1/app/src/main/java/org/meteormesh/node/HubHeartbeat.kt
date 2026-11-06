package org.meteormesh.node

import android.content.Context
import android.os.Build
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.NetworkInterface
import java.net.URL
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSession
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

/** Small outbound HTTPS heartbeat. Never performs camera start, file removal or shell actions. */
object HubHeartbeat {
    data class Command(val id: String,val action: String)
    private val random=SecureRandom()
    private val hex=Regex("[a-f0-9]{32}")
    private fun bytes(hex: String): ByteArray = hex.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
    private fun fingerprint(cert: X509Certificate) = MessageDigest.getInstance("SHA-256").digest(cert.encoded)
    private fun address(): String = try {
        NetworkInterface.getNetworkInterfaces().toList()
            .flatMap { it.inetAddresses.toList() }
            .firstOrNull { it is Inet4Address && !it.isLoopbackAddress && it.isSiteLocalAddress }
            ?.hostAddress ?: ""
    } catch (_: Exception) { "" }
    private fun trust(pin: ByteArray): X509TrustManager = object:X509TrustManager {
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        override fun checkClientTrusted(chain:Array<X509Certificate>?,authType:String?) {
            throw CertificateException("Not a client trust manager")
        }
        override fun checkServerTrusted(chain:Array<X509Certificate>?,authType:String?) {
            val leaf=chain?.firstOrNull() ?: throw CertificateException("Server certificate missing")
            leaf.checkValidity()
            if (!MessageDigest.isEqual(fingerprint(leaf),pin))
                throw CertificateException("MeteorHub certificate pin mismatch")
        }
    }
    fun exchange(context: Context, lanListening: Boolean): Command? {
        val cfg=HubControlConfig.load(context) ?: return null
        val id=HubControlConfig.deviceId() ?: return null
        val pin=bytes(cfg.fingerprint)
        val ssl=SSLContext.getInstance("TLS").also { it.init(null,arrayOf<TrustManager>(trust(pin)),SecureRandom()) }
        val v=HostnameVerifier { host:String, session:SSLSession ->
            if (host != cfg.ip) false else try {
                val peer=session.peerCertificates.first() as X509Certificate
                MessageDigest.isEqual(fingerprint(peer),pin)
            } catch (_:Exception) { false }
        }
        val nonce=ByteArray(16).also { random.nextBytes(it) }
            .joinToString("") { "%02x".format(it.toInt() and 255) }
        val state=context.getSharedPreferences("meteor-state",Context.MODE_PRIVATE)
            .getString("state","unknown") ?: "unknown"
        val canonical=when {
            state in setOf("idle","armed_idle","recording","stopped","error") -> state
            state.startsWith("recording:") || state.startsWith("retrying_") -> "recording"
            state.startsWith("stopped_low_storage") -> "stopped"
            state.startsWith("stopped_rotation_error") -> "error"
            else -> "unknown"
        }
        val segments=SegmentStore.get(context)
        val status=JSONObject().apply {
            put("camera_state",canonical)
            put("lan_listening",lanListening)
            put("ready_segments",segments.listReady().length())
            put("free_mib",(segments.freeBytes()/(1024L*1024L)).coerceIn(0,1000000).toInt())
            put("app_version",BuildConfig.VERSION_NAME)
            put("lan_ip",address())
        }
        val request=JSONObject().apply {
            put("device_id",id)
            put("timestamp_ms",System.currentTimeMillis())
            put("nonce",nonce)
            put("status",status)
            put("acked_command_id",HubControlConfig.lastAck(context) ?: JSONObject.NULL)
        }
        val connection=URL("https://${cfg.ip}:8443/v1/heartbeat").openConnection() as HttpsURLConnection
        connection.sslSocketFactory=ssl.socketFactory
        connection.hostnameVerifier=v
        connection.requestMethod="POST"
        connection.connectTimeout=5000
        connection.readTimeout=6000
        connection.doOutput=true
        connection.setRequestProperty("Content-Type","application/json")
        connection.setRequestProperty("X-Meteor-Device",id)
        connection.setRequestProperty("Authorization","Bearer ${cfg.token}")
        try {
            connection.outputStream.use { it.write(request.toString().toByteArray(Charsets.UTF_8)) }
            if (connection.responseCode != HttpURLConnection.HTTP_OK) {
                throw IllegalStateException("Hub response HTTP ${connection.responseCode}")
            }
            val out=ByteArrayOutputStream()
            connection.inputStream.use { input ->
                val buffer=ByteArray(1024)
                while(true) {
                    val n=input.read(buffer)
                    if(n<0) break
                    if(out.size()+n>8192) throw IllegalStateException("Hub response too long")
                    out.write(buffer,0,n)
                }
            }
            val reply=JSONObject(out.toString("UTF-8"))
            if (!reply.optBoolean("ok",false) || reply.optInt("interval_seconds",0)!=60)
                throw IllegalStateException("Invalid Hub response")
            val cmd=reply.optJSONObject("command") ?: return null
            val cmdId=cmd.optString("id", "")
            val action=cmd.optString("action", "")
            return if (cmdId.matches(hex) && action=="restart_listener") Command(cmdId,action) else null
        } finally { connection.disconnect() }
    }
}
