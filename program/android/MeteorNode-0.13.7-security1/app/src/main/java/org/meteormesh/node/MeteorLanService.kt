package org.meteormesh.node

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import java.util.concurrent.Executors

/**
 * User-armed persistent LAN listener for direct HP G62 access.
 * No camera launch, shell execution, deletion or hidden background service creation.
 * Android may stop even a foreground service; force-stop needs manual re-arm.
 * No automatic phone-to-hub heartbeat or inbound WAN connectivity in this stage.
 */
class MeteorLanService : Service() {
    private var server: LocalHttpServer? = null
    private val handler = Handler(Looper.getMainLooper())
    private var startedAtElapsed = 0L
    private var nextRestartAtElapsed = 0L
    private var attempt = 0
    private var shuttingDown = false
    private var lanCpuWakeLock: PowerManager.WakeLock? = null
    private var lanWifiLock: WifiManager.WifiLock? = null
    private val controlExecutor = Executors.newSingleThreadExecutor()
    @Volatile private var heartbeatInFlight = false
    private var heartbeatInitialized = false
    private var lastRemoteRestartMs = 0L
    private val outboundHeartbeat = object : Runnable {
        override fun run() {
            if (shuttingDown) return
            if (RemoteSettings.enabled(this@MeteorLanService) &&
                HubControlConfig.load(this@MeteorLanService) != null && !heartbeatInFlight) {
                heartbeatInFlight = true
                val listening=server?.isListening()==true
                controlExecutor.execute {
                    try {
                        val command=HubHeartbeat.exchange(this@MeteorLanService,listening)
                        HubControlConfig.note(this@MeteorLanService,"HTTPS_CONNECTED")
                        if (command != null) handler.post {
                            if (shuttingDown || command.action != "restart_listener") return@post
                            if (HubControlConfig.lastAck(this@MeteorLanService)==command.id) return@post
                            val now=SystemClock.elapsedRealtime()
                            // Never trigger camera service; one LAN recovery every two minutes.
                            if (now - lastRemoteRestartMs >= 120_000L) {
                                lastRemoteRestartMs=now
                                restartListener(now)
                                HubControlConfig.saveAck(this@MeteorLanService,command.id)
                            }
                        }
                    } catch (e: Exception) {
                        // Do not persist tokens or HTTPS request content in the error journal.
                        HubControlConfig.note(this@MeteorLanService,"HTTPS_RETRY_${e.javaClass.simpleName}")
                    } finally { heartbeatInFlight=false }
                }
            }
            handler.postDelayed(this,60_000L)
        }
    }

    private val watchdog = object : Runnable {
        override fun run() {
            if (shuttingDown) return
            if (!RemoteSettings.enabled(this@MeteorLanService)) {
                stopSelf()
                return
            }
            val now = SystemClock.elapsedRealtime()
            val s = server
            if (s == null || (!s.isListening() && now - startedAtElapsed > 15_000)) {
                if (now >= nextRestartAtElapsed) restartListener(now)
            } else if (s.isListening()) {
                attempt = 0
            }
            handler.postDelayed(this, 30_000L)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!RemoteSettings.enabled(this)) {
            stopSelf()
            return START_NOT_STICKY
        }
        try {
            val notifications = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            notifications.createNotificationChannel(NotificationChannel(
                "meteor-lan", "MeteorNode yerel ag", NotificationManager.IMPORTANCE_LOW
            ))
            val reopen = PendingIntent.getActivity(
                this, 0, Intent(this, MainActivity::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            val notification = Notification.Builder(this, "meteor-lan")
                .setSmallIcon(android.R.drawable.ic_menu_share)
                .setContentTitle("MeteorNode LAN gozlemi")
                .setContentText("LAN servisi acik; ekran kapanabilir")
                .setContentIntent(reopen)
                .setOngoing(true)
                .build()
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(422, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
            } else {
                startForeground(422, notification)
            }
            acquireConnectivityLocks()
            if (server == null) {
                restartListener(SystemClock.elapsedRealtime())
                handler.removeCallbacks(watchdog)
                handler.postDelayed(watchdog, 30_000L)
            }
            if (!heartbeatInitialized) {
                heartbeatInitialized=true
                handler.postDelayed(outboundHeartbeat, 3_000L)
            }
        } catch (e: Exception) {
            noteError("foreground_start_failed: ${e.javaClass.simpleName}")
            Log.e("MeteorNodeHTTP", "LAN service failed", e)
            stopSelf()
            return START_NOT_STICKY
        }
        // OS MAY recreate a previously user-started service; does not bypass force-stop.
        return START_STICKY
    }

    /** Keep the user-armed LAN listener's CPU and Wi-Fi active while the screen is off. */
    private fun acquireConnectivityLocks() {
        val prefs = getSharedPreferences("meteor-lan", MODE_PRIVATE)
        prefs.edit().remove("connectivity_lock_error").apply()
        try {
            val power = getSystemService(Context.POWER_SERVICE) as PowerManager
            val lock = lanCpuWakeLock ?: power.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK, "$packageName:LAN"
            ).apply { setReferenceCounted(false) }.also { lanCpuWakeLock = it }
            if (!lock.isHeld) lock.acquire()
            prefs.edit().putBoolean("cpu_wake_lock_held", lock.isHeld).apply()
        } catch (e: Exception) {
            prefs.edit().putBoolean("cpu_wake_lock_held", false)
                .putString("connectivity_lock_error", "cpu:${e.javaClass.simpleName}").apply()
            Log.w("MeteorNodeHTTP", "Could not acquire LAN CPU wake lock", e)
        }

        try {
            val wifi = getSystemService(Context.WIFI_SERVICE) as WifiManager
            val mode = wifiLockMode()
            val lock = lanWifiLock ?: wifi.createWifiLock(mode, "$packageName:LAN").apply {
                setReferenceCounted(false)
            }.also { lanWifiLock = it }
            if (!lock.isHeld) lock.acquire()
            prefs.edit().putBoolean("wifi_lock_held", lock.isHeld)
                .putString("wifi_lock_mode", wifiLockModeName()).apply()
        } catch (e: Exception) {
            prefs.edit().putBoolean("wifi_lock_held", false)
                .putString("connectivity_lock_error", "wifi:${e.javaClass.simpleName}").apply()
            Log.w("MeteorNodeHTTP", "Could not acquire LAN Wi-Fi lock", e)
        }
    }

    @Suppress("DEPRECATION")
    private fun wifiLockMode(): Int = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
        // API 29-33 keeps Wi-Fi out of power-save while screen-off. Android 14+
        // may map this to low-latency mode; the user battery setting remains relevant.
        WifiManager.WIFI_MODE_FULL_HIGH_PERF
    } else {
        WifiManager.WIFI_MODE_FULL
    }

    private fun wifiLockModeName(): String = when {
        Build.VERSION.SDK_INT < Build.VERSION_CODES.Q -> "FULL"
        Build.VERSION.SDK_INT < 34 -> "FULL_HIGH_PERF"
        else -> "FULL_HIGH_PERF_PLATFORM_MAY_MAP_TO_LOW_LATENCY"
    }

    private fun releaseConnectivityLocks() {
        try {
            lanWifiLock?.let { if (it.isHeld) it.release() }
        } catch (e: Exception) {
            Log.w("MeteorNodeHTTP", "Could not release LAN Wi-Fi lock", e)
        } finally {
            lanWifiLock = null
        }
        try {
            lanCpuWakeLock?.let { if (it.isHeld) it.release() }
        } catch (e: Exception) {
            Log.w("MeteorNodeHTTP", "Could not release LAN CPU wake lock", e)
        } finally {
            lanCpuWakeLock = null
        }
        getSharedPreferences("meteor-lan", MODE_PRIVATE).edit()
            .putBoolean("cpu_wake_lock_held", false)
            .putBoolean("wifi_lock_held", false).apply()
    }

    private fun restartListener(now: Long) {
        // No unbounded fast retry loop, no cross-service camera actions.
        server?.stop()
        server = null
        attempt = (attempt + 1).coerceAtMost(100000)
        val exponent = (attempt - 1).coerceIn(0, 4)
        val backoffMs = 30_000L * (1L shl exponent) // 30s,60s,120s,240s,480s
        nextRestartAtElapsed = now + backoffMs
        startedAtElapsed = now
        getSharedPreferences("meteor-lan", MODE_PRIVATE).edit()
            .putInt("recovery_attempts", attempt)
            .putLong("recovery_last_ms", System.currentTimeMillis())
            .apply()
        try {
            server = LocalHttpServer(this, SegmentStore.get(this)).also { it.start() }
        } catch (e: Exception) {
            noteError("listener_recovery_failed: ${e.javaClass.simpleName}")
            Log.e("MeteorNodeHTTP", "Listener recovery failed", e)
        }
    }

    private fun noteError(message: String) {
        getSharedPreferences("meteor-lan", MODE_PRIVATE).edit()
            .putString("server_status", "error")
            .putString("last_http_error", message).apply()
    }

    override fun onDestroy() {
        shuttingDown = true
        handler.removeCallbacks(watchdog)
        handler.removeCallbacks(outboundHeartbeat)
        controlExecutor.shutdownNow()
        server?.stop()
        server = null
        releaseConnectivityLocks()
        super.onDestroy()
    }

    override fun onTimeout(startId: Int, fgsType: Int) {
        // Defensive behavior if OS imposes any FGS deadline in the future.
        noteError("foreground_service_timeout")
        stopSelf()
    }
}
