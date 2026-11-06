package org.meteormesh.node

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.net.Uri
import android.os.PowerManager
import android.widget.Button
import android.widget.EditText
import android.text.InputType
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import java.net.Inet4Address
import java.net.NetworkInterface

/** Small, single-screen phone UI. Recording/storage logic is unchanged from 0.9. */
class MainActivity : Activity() {
    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 24, 32, 36)
        }
        fun label(value: String, size: Float = 16f) = TextView(this).apply {
            text = value
            textSize = size
            setPadding(0, 12, 0, 12)
        }
        fun button(title: String, action: () -> Unit) {
            layout.addView(Button(this).apply {
                text = title
                setOnClickListener { action() }
            })
        }

        layout.addView(label("MeteorNode 0.13.6", 25f))
        layout.addView(label("Yerel ag kontrolu · 3 telefon · HTTP 8080", 14f))

        button("1  LAN + UZAKTAN KONTROLU HAZIRLA") {
            if (!hasCameraPermission()) {
                requestNeededPermissions()
            } else {
                try {
                    RemoteSettings.setEnabled(this, true)
                    // Visible user action; Android camera foreground restrictions preserved.
                    startForegroundService(Intent(this, MeteorCaptureService::class.java).apply {
                        action = MeteorCaptureService.ACTION_ARM
                    })
                    startForegroundService(Intent(this, MeteorLanService::class.java))
                    Toast.makeText(this, "LAN ve kamera hazirlaniyor", Toast.LENGTH_SHORT).show()
                    refreshStatus()
                } catch (e: Exception) {
                    status.text = "Hazirlama hatasi: ${e.javaClass.simpleName}: ${e.message}"
                }
            }
        }
        button("HP G62 HTTPS ESLESTIRME") {
            val dev=HubControlConfig.deviceId()
            if (dev == null) {
                Toast.makeText(this,"Bilinmeyen cihaz modeli",Toast.LENGTH_LONG).show()
            } else {
                val content=LinearLayout(this).apply {
                    orientation=LinearLayout.VERTICAL
                    setPadding(32, 16, 32, 8)
                }
                val ip=EditText(this).apply { hint="HP G62 IP (192.168.x.x)";setSingleLine(true) }
                val pin=EditText(this).apply {
                    hint="Ortak sifre: 0000"
                    setText("0000")
                    setSingleLine(true)
                    inputType=InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD
                }
                listOf(ip,pin).forEach { content.addView(it) }
                AlertDialog.Builder(this).setTitle("MeteorHub ortak sifre: $dev")
                    .setView(content)
                    .setNegativeButton("Vazgec",null)
                    .setPositiveButton("Kaydet") { _,_ ->
                        if (HubControlConfig.save(this,ip.text.toString(),pin.text.toString())) {
                            Toast.makeText(this,"Eslesme kaydedildi; LAN servisi acik olmali",Toast.LENGTH_LONG).show()
                            refreshStatus()
                        } else {
                            Toast.makeText(this,"HP IP veya ortak sifre hatali (0000)",Toast.LENGTH_LONG).show()
                        }
                    }.show()
            }
        }
        button("HP G62 ESLESMESINI SIL") {
            AlertDialog.Builder(this).setTitle("HP G62 bilgileri kaldirilsin mi?")
                .setMessage("Yalniz kimlik bilgileri silinir, video ve kamera kaydi etkilenmez.")
                .setNegativeButton("Vazgec",null)
                .setPositiveButton("Sil") { _,_ -> HubControlConfig.clear(this);refreshStatus() }.show()
        }
        button("KAYDI BASLAT") {
            if (!hasCameraPermission()) {
                requestNeededPermissions()
            } else {
                try {
                    startForegroundService(Intent(this, MeteorCaptureService::class.java).apply {
                        action = MeteorCaptureService.ACTION_START
                        putExtra(MeteorCaptureService.EXTRA_PROFILE, RemoteSettings.profile(this@MainActivity))
                    })
                    refreshStatus()
                } catch (e: Exception) {
                    status.text = "Kayit baslatma hatasi: ${e.javaClass.simpleName}: ${e.message}"
                }
            }
        }
        button("KAYDI DURDUR") {
            AlertDialog.Builder(this)
                .setTitle("Kayit durdurulsun mu?")
                .setMessage("Aktif video parcasi normal yolla kapatilacak. Eski MP4 dosyalari silinmeyecek.")
                .setNegativeButton("Vazgec", null)
                .setPositiveButton("Durdur") { _, _ ->
                    try {
                        startService(Intent(this, MeteorCaptureService::class.java).apply {
                            action = MeteorCaptureService.ACTION_STOP
                        })
                    } catch (e: Exception) {
                        status.text = "Durdurma hatasi: ${e.message}"
                    }
                }.show()
        }
        button("DEPOLAMA YONETIMI IZNI") {
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                openAndroidSettings(Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                    Uri.parse("package:$packageName")))
            } else {
                Toast.makeText(this, "Bu Android surumunde ek izin gerekmiyor", Toast.LENGTH_SHORT).show()
            }
        }
        button("DEPOLAMA YONETICISI ANAHTARINI GOR") {
            AlertDialog.Builder(this).setTitle("Sadece HP G62'de saklayin")
                .setMessage("Yalniz ilk kurulumda Linux'a girilecek. Bu anahtara sahip kisi ortak depolamadaki dosyalari silebilir.\n\n" + RemoteStorage.adminKey(this))
                .setPositiveButton("Kapat", null).show()
        }
        button("DURUMU YENILE") { refreshStatus() }
        status = label("Durum okunuyor...", 15f)
        status.setTextIsSelectable(true)
        layout.addView(status)

        button("IZINLER VE TEKNIK AYARLAR") {
            val choices = arrayOf(
                "Kamera ve bildirim izinlerini iste",
                "MeteorNode icin pil optimizasyonu istisnasi iste",
                "Xiaomi arka plan ayarlarini acikla",
                "Sistem ayari izni ekranini ac (istege bagli)",
                "Kamera ozelliklerini gor",
                "LAN ve uzaktan kontrolu kapat"
            )
            AlertDialog.Builder(this).setTitle("Ek ayarlar")
                .setItems(choices) { _, selected ->
                    when (selected) {
                        0 -> requestNeededPermissions()
                        1 -> requestBatteryOptimizationExemption()
                        2 -> AlertDialog.Builder(this).setTitle("Xiaomi arka plan calismasi")
                            .setMessage(
                                "MeteorNode LAN servisi ekran kapaliyken CPU ve Wi-Fi kilidi kullanir. " +
                                "Xiaomi'de baglantinin uykuya girmemesi icin Uygulama bilgisi > Pil tasarrufu > Kisitsiz yapin; " +
                                "Otomatik baslat iznini acin. Bu ayarlari uygulama kendisi degistiremez."
                            )
                            .setNegativeButton("Kapat", null)
                            .setPositiveButton("Uygulama bilgisi") { _, _ ->
                                openAndroidSettings(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                    Uri.parse("package:$packageName")))
                            }.show()
                        3 -> openAndroidSettings(Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS,
                            Uri.parse("package:$packageName")))
                        4 -> AlertDialog.Builder(this).setTitle("Kamera ayrintilari")
                            .setMessage(try { CameraInventory.json(this).toString(2) }
                                catch (e: Exception) { e.message ?: "Bilgi alinamadi" })
                            .setPositiveButton("Kapat", null).show()
                        5 -> AlertDialog.Builder(this)
                            .setTitle("LAN erisimini kapat")
                            .setMessage("Kayit etkilenmez; Linux baglantisi kesilir.")
                            .setNegativeButton("Vazgec", null)
                            .setPositiveButton("Kapat") { _, _ ->
                                RemoteSettings.setEnabled(this, false)
                                stopService(Intent(this, MeteorLanService::class.java))
                                refreshStatus()
                            }.show()
                    }
                }.setNegativeButton("Kapat", null).show()
        }

        layout.addView(label(
            "TEST: Anahtar 32 sifir olarak sabittir. Degistirilemez ve gizli degildir. " +
            "HP kontrol sifresi 0000; yalniz guvenilir Wi-Fi kullanin. 8080 ve 8443 portlarini internete acmayin.", 13f))
        setContentView(ScrollView(this).apply { addView(layout) })
        requestNeededPermissions()
        refreshStatus()
    }

    override fun onResume() {
        super.onResume()
        if (::status.isInitialized) refreshStatus()
    }

    private fun hasCameraPermission() =
        checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED

    private fun requestNeededPermissions() {
        val needed = mutableListOf<String>()
        if (!hasCameraPermission()) needed.add(Manifest.permission.CAMERA)
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            needed.add(Manifest.permission.POST_NOTIFICATIONS)
        if (needed.isNotEmpty()) requestPermissions(needed.toTypedArray(), 201)
    }

    private fun openAndroidSettings(intent: Intent) {
        try { startActivity(intent) }
        catch (e: Exception) { status.text = "Android ayar ekrani acilamadi: ${e.message}" }
    }

    private fun requestBatteryOptimizationExemption() {
        val power = getSystemService(Context.POWER_SERVICE) as PowerManager
        if (power.isIgnoringBatteryOptimizations(packageName)) {
            Toast.makeText(this, "MeteorNode pil optimizasyonu istisnasinda", Toast.LENGTH_LONG).show()
            return
        }
        AlertDialog.Builder(this)
            .setTitle("MeteorNode ag baglantisini korusun mu?")
            .setMessage("Ekran kapaliyken LAN listener ve telefon baglantisinin uykuya alinmamasi icin Android istisnasi istenir. Pil tuketimi artabilir; son karari Android ekrani size birakacak.")
            .setNegativeButton("Vazgec", null)
            .setPositiveButton("Android ayarini ac") { _, _ ->
                openAndroidSettings(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:$packageName")))
            }.show()
    }

    private fun refreshStatus() {
        if (!::status.isInitialized) return
        val state = getSharedPreferences("meteor-state", MODE_PRIVATE)
        val lan = getSharedPreferences("meteor-lan", MODE_PRIVATE)
        val pub = getSharedPreferences("meteor-public-export", MODE_PRIVATE)
        val power = getSystemService(Context.POWER_SERVICE) as PowerManager
        val store = SegmentStore.get(this)
        val current = RemoteSettings.current(this)
        status.text = buildString {
            append("Cihaz: ${Build.MODEL} (${Build.DEVICE})\n")
            append("IP: ${lanIp()}:8080\n")
            append("LAN: ${lan.getString("server_status", "not-started")}\n")
            append("Uyku korumasi: CPU ${lan.getBoolean("cpu_wake_lock_held", false)}, Wi-Fi ${lan.getBoolean("wifi_lock_held", false)}\n")
            append("Pil kisitlama istisnasi: ${if (power.isIgnoringBatteryOptimizations(packageName)) "ACIK" else "YOK"}\n")
            lan.getString("connectivity_lock_error", null)?.let { append("Uyku korumasi hatasi: $it\n") }
            append("Depolama izni: ${RemoteStorage.granted()}\n")
            append("Kayit: ${state.getString("state", "idle")}\n")
            append("Dosya gecisi: ${state.getString("recording_rotation_state", "idle")} (hedef ${state.getLong("recording_segment_target_ms", 0L) / 1000} sn; PTS/anahtar kare gecisi, cihazda dogrulanmadi)\n")
            append("Aktif segment: ${state.getString("recording_active_segment_id", "yok")}\n")
            val freeBytes = store.freeBytes()
            append("Korunan bos alan: ${freeBytes / (1024L*1024L*1024L)} GiB / 32 GiB esik\n")
            append("Linux kontrolu: ${if (RemoteSettings.enabled(this@MainActivity)) "ACIK" else "KAPALI"}\n")
            append("Kamera hazir: ${MeteorCaptureService.readyForRemote(this@MainActivity)}\n")
            append("Son komut: ${RemoteSettings.lastCommand(this@MainActivity)}\n")
            append("Profil: ${current.optString("profile")}, ${current.optInt("bitrate_mbps")} Mbps, ${current.optInt("segment_seconds")} sn\n")
            append("Hazir MP4: ${store.listReady().length()}\n")
            append("Video deposu: Uygulama dahili / tek kopya\n")
            append("Bos alan: ${freeBytes / (1024*1024)} MiB\n")
            append("Disari kopyalama: KAPALI\n")
            append("Hata: ${state.getString("last_error", "yok")}\n")
            append("LAN hata: ${lan.getString("last_http_error", "yok")}\n")
            append("HP HTTPS eslesme: ${if (HubControlConfig.load(this@MainActivity)!=null) "AYARLI" else "YOK"}\n")
            append("HP HTTPS son durum: ${getSharedPreferences("meteor-hub-control", MODE_PRIVATE).getString("last_status","bekliyor")}\n")
            append("Dahili MP4, Linux GET /api/v1/segments ile alinabilir")
        }
    }

    private fun lanIp(): String = try {
        NetworkInterface.getNetworkInterfaces().toList()
            .flatMap { it.inetAddresses.toList() }
            .firstOrNull { it is Inet4Address && !it.isLoopbackAddress && it.isSiteLocalAddress }
            ?.hostAddress ?: "telefon-ip"
    } catch (_: Exception) { "telefon-ip" }
}
