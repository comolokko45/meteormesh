package org.meteormesh.mobile

import android.app.Activity
import android.content.Context
import android.content.res.Configuration
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.RenderProcessGoneDetail
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import org.json.JSONArray
import org.json.JSONObject

class MainActivity : Activity() {
    private val monitorHost = BuildConfig.MONITOR_HOST
    private val monitorUrl = "https://$monitorHost/mobile"
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var connectivity: ConnectivityManager
    private lateinit var webView: WebView
    private lateinit var swipeRefresh: SwipeRefreshLayout
    private lateinit var overlay: LinearLayout
    private lateinit var overlayTitle: TextView
    private lateinit var overlayMessage: TextView
    private lateinit var statusText: TextView
    private lateinit var updatedText: TextView
    private var resumed = false
    private var pageReady = false
    private var retryDelayMs = 5_000L
    private var retryPending = false
    private var statusPolling = false
    private var mainFrameError = false

    private val statusPoll = object : Runnable {
        override fun run() {
            if (!resumed) return
            updateStatusFromMonitorPage()
            handler.postDelayed(this, STATUS_POLL_MS)
        }
    }

    private val retryTask = Runnable {
        retryPending = false
        if (resumed && !pageReady) loadMonitor(showLoading = false)
    }

    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            handler.postDelayed({
                if (resumed && !pageReady) loadMonitor(showLoading = false)
            }, 1_200L)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        connectivity = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        buildInterface()
        configureWebView()
        WindowCompat.setDecorFitsSystemWindows(window, false)
        ViewCompat.setOnApplyWindowInsetsListener(rootView) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(0, bars.top, 0, bars.bottom)
            insets
        }
        val isNight = (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
        WindowInsetsControllerCompat(window, rootView).apply {
            isAppearanceLightStatusBars = !isNight
            isAppearanceLightNavigationBars = !isNight
        }
        try {
            connectivity.registerDefaultNetworkCallback(networkCallback)
        } catch (_: RuntimeException) {
            // The normal WebView retry loop still works if the OS rejects a callback.
        }
    }

    private lateinit var rootView: LinearLayout

    private fun buildInterface() {
        val night = (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
        val background = if (night) Color.rgb(16, 21, 29) else Color.rgb(238, 242, 244)
        val panel = if (night) Color.rgb(25, 34, 45) else Color.WHITE
        val foreground = if (night) Color.rgb(237, 243, 247) else Color.rgb(23, 35, 45)
        val muted = if (night) Color.rgb(159, 176, 191) else Color.rgb(86, 103, 118)
        val accent = Color.rgb(115, 210, 181)

        window.statusBarColor = Color.TRANSPARENT
        window.navigationBarColor = background
        window.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS)

        rootView = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(background)
        }

        val toolbar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), dp(8), dp(12), dp(8))
            setBackgroundColor(panel)
        }
        val title = TextView(this).apply {
            text = "MeteorMesh Mobile"
            textSize = 19f
            setTextColor(foreground)
            typeface = Typeface.DEFAULT_BOLD
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        val refreshButton = Button(this).apply {
            text = "↻"
            textSize = 24f
            minWidth = dp(48)
            minimumWidth = dp(48)
            minHeight = dp(44)
            minimumHeight = dp(44)
            setPadding(0, 0, 0, 0)
            setTextColor(foreground)
            this.background = rounded(panel, muted, dp(12))
            contentDescription = "Şimdi yenile"
            setOnClickListener { refreshNow() }
        }
        toolbar.addView(title)
        val managementButton = Button(this).apply {
            text = "Yönet"
            contentDescription = "Güvenli uzaktan yönetim"
            setOnClickListener { webView.loadUrl("https://$monitorHost/manage") }
        }
        toolbar.addView(managementButton)
        toolbar.addView(refreshButton)
        // The mobile page owns its responsive header and navigation.

        val statusPanel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(7), dp(16), dp(8))
            setBackgroundColor(panel)
        }
        statusText = TextView(this).apply {
            text = "Tailscale yolu ve G62 kontrol ediliyor…"
            textSize = 13f
            setTextColor(accent)
            typeface = Typeface.DEFAULT_BOLD
        }
        updatedText = TextView(this).apply {
            text = "Son veri: henüz alınmadı"
            textSize = 11f
            setTextColor(muted)
            setPadding(0, dp(2), 0, 0)
        }
        statusPanel.addView(statusText)
        statusPanel.addView(updatedText)
        // The mobile page renders its own live connection strip.

        val content = FrameLayout(this)
        swipeRefresh = SwipeRefreshLayout(this).apply {
            setColorSchemeColors(accent, Color.rgb(41, 155, 125))
            setOnRefreshListener { refreshNow() }
        }
        webView = WebView(this).apply {
            setBackgroundColor(background)
            isVerticalScrollBarEnabled = true
            overScrollMode = View.OVER_SCROLL_NEVER
        }
        swipeRefresh.addView(webView, ViewGroup.LayoutParams(-1, -1))
        content.addView(swipeRefresh, FrameLayout.LayoutParams(-1, -1))

        overlay = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(dp(28), dp(24), dp(28), dp(24))
            setBackgroundColor(background)
        }
        val errorCard = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(dp(24), dp(24), dp(24), dp(24))
            this.background = rounded(panel, muted, dp(18))
        }
        val spinner = ProgressBar(this).apply {
            isIndeterminate = true
            indeterminateTintList = android.content.res.ColorStateList.valueOf(accent)
        }
        overlayTitle = TextView(this).apply {
            text = "G62'ye bağlanılıyor"
            textSize = 18f
            setTextColor(foreground)
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
            setPadding(0, dp(14), 0, dp(8))
        }
        overlayMessage = TextView(this).apply {
            text = "Tailscale özel ağı üzerinden güvenli HTTPS bağlantısı kuruluyor."
            textSize = 14f
            setTextColor(muted)
            gravity = Gravity.CENTER
        }
        val retryButton = Button(this).apply {
            text = "Yeniden dene"
            setTextColor(Color.rgb(8, 38, 30))
            this.background = rounded(accent, accent, dp(12))
            setOnClickListener { refreshNow() }
        }
        errorCard.addView(spinner, LinearLayout.LayoutParams(dp(42), dp(42)))
        errorCard.addView(overlayTitle, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        errorCard.addView(overlayMessage, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        errorCard.addView(retryButton, LinearLayout.LayoutParams(-1, dp(48)).apply { topMargin = dp(18) })
        overlay.addView(errorCard, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        content.addView(overlay, FrameLayout.LayoutParams(-1, -1))

        rootView.addView(content, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(rootView)
    }

    private fun configureWebView() {
        WebView.setWebContentsDebuggingEnabled(false)
        webView.settings.apply {
            javaScriptEnabled = true // Existing monitor UI uses inline JavaScript and fetch.
            domStorageEnabled = true // The monitor uses localStorage for its theme toggle.
            allowFileAccess = false
            allowContentAccess = false
            allowFileAccessFromFileURLs = false
            allowUniversalAccessFromFileURLs = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            javaScriptCanOpenWindowsAutomatically = false
            setSupportMultipleWindows(false)
            safeBrowsingEnabled = true
            builtInZoomControls = true
            displayZoomControls = false
        }
        webView.setDownloadListener { _, _, _, _, _ ->
            // The monitor is read-only; downloads are intentionally unavailable.
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onCreateWindow(view: WebView?, isDialog: Boolean, isUserGesture: Boolean, resultMsg: android.os.Message?): Boolean = false
            override fun onPermissionRequest(request: android.webkit.PermissionRequest) {
                request.deny()
            }
        }
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                return !isTrustedUrl(request.url)
            }

            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                val uri = request.url
                if (isTrustedUrl(uri)) return null
                return WebResourceResponse("text/plain", "UTF-8", 403, "Blocked", emptyMap(), null)
            }

            override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
                mainFrameError = false
                pageReady = false
                statusText.text = "G62 monitörüne güvenli bağlantı kuruluyor…"
                if (overlay.visibility != View.VISIBLE) swipeRefresh.isRefreshing = true
            }

            override fun onPageFinished(view: WebView, url: String) {
                if (mainFrameError) return
                if (!isTrustedUrl(Uri.parse(url))) {
                    showConnectionError("Adres doğrulanmadı", "Yalnız canlı Tailscale Serve adresine izin verilir.")
                    return
                }
                pageReady = true
                retryDelayMs = 5_000L
                retryPending = false
                handler.removeCallbacks(retryTask)
                overlay.visibility = View.GONE
                swipeRefresh.isRefreshing = false
                installBackNavigationHook()
                handler.postDelayed({ updateStatusFromMonitorPage() }, 1_500L)
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) {
                    mainFrameError = true
                    pageReady = false
                    val message = connectionFailureMessage()
                    showConnectionError(message.first, message.second)
                    scheduleRetry()
                }
            }

            override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
                if (request.isForMainFrame && response.statusCode >= 400) {
                    mainFrameError = true
                    pageReady = false
                    showConnectionError("G62 monitörü yanıt vermedi", "HTTPS yanıt kodu ${response.statusCode}. Veri eskiymiş gibi gösterilmiyor.")
                    scheduleRetry()
                }
            }

            override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: android.net.http.SslError) {
                handler.cancel()
                mainFrameError = true
                pageReady = false
                showConnectionError("TLS sertifikası doğrulanamadı", "Güvenlik doğrulaması atlanmadı. Tailscale Serve sertifikasını ve cihaz saatini kontrol edin.")
            }

            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                mainFrameError = true
                pageReady = false
                handler.removeCallbacks(retryTask)
                retryPending = false
                (view.parent as? ViewGroup)?.removeView(view)
                view.destroy()
                if (isFinishing || isDestroyed) return true
                webView = WebView(this@MainActivity).apply {
                    isVerticalScrollBarEnabled = true
                    overScrollMode = View.OVER_SCROLL_NEVER
                }
                swipeRefresh.addView(webView, ViewGroup.LayoutParams(-1, -1))
                configureWebView()
                showConnectionError("Görüntüleme motoru yeniden başladı", "Güvenli biçimde yeniden bağlanılıyor.")
                scheduleRetry()
                return true
            }
        }
    }

    private fun isTrustedUrl(uri: Uri): Boolean =
        uri.scheme.equals("https", ignoreCase = true) && uri.host.equals(monitorHost, ignoreCase = true) &&
            (uri.port == -1 || uri.port == 443) && uri.userInfo == null

    private fun loadMonitor(showLoading: Boolean) {
        if (showLoading) {
            overlay.visibility = View.VISIBLE
            overlayTitle.text = "G62'ye bağlanılıyor"
            overlayMessage.text = "Tailscale özel ağı üzerinden güvenli HTTPS bağlantısı kuruluyor."
        }
        webView.loadUrl(monitorUrl)
    }

    private fun refreshNow() {
        retryDelayMs = 5_000L
        retryPending = false
        handler.removeCallbacks(retryTask)
        swipeRefresh.isRefreshing = true
        if (pageReady) webView.reload() else loadMonitor(showLoading = true)
    }

    private fun showConnectionError(title: String, message: String) {
        overlayTitle.text = title
        overlayMessage.text = message
        overlay.visibility = View.VISIBLE
        swipeRefresh.isRefreshing = false
        statusText.text = title
        updatedText.text = "Son veri güncellemesi doğrulanmıyor"
    }

    private fun connectionFailureMessage(): Pair<String, String> {
        val capabilities = connectivity.getNetworkCapabilities(connectivity.activeNetwork)
        if (capabilities == null || !capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) {
            return "İnternet bağlantısı yok" to "Telefonun ağ bağlantısını kontrol edip yeniden deneyin."
        }
        if (!capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) {
            return "Tailscale bağlantısı görünmüyor" to "Tailscale uygulamasını açıp tailnet'e bağlanın; ardından yeniden deneyin."
        }
        return "G62 erişilemiyor" to "VPN bağlantısı var ancak G62 HTTPS adresine erişilemiyor. VPN bağlantısı tek başına Tailscale erişimini doğrulamaz. G62'nin çevrimiçi olduğunu kontrol edin."
    }

    private fun scheduleRetry() {
        if (!resumed || retryPending) return
        retryPending = true
        val delay = retryDelayMs
        retryDelayMs = (retryDelayMs * 2).coerceAtMost(120_000L)
        handler.postDelayed(retryTask, delay)
    }

    private fun installBackNavigationHook() {
        val script = """
            (function(){
              if(window.__mmAppHistoryInstalled)return;
              window.__mmAppHistoryInstalled=true;
              window.__mmAppBackStack=[];
              var nav=document.getElementById('nav');
              if(nav)nav.addEventListener('click',function(e){
                var b=e.target.closest('.navbtn');
                if(b&&window.current&&b.dataset.page!==window.current)window.__mmAppBackStack.push(window.current);
              },true);
              window.__mmAndroidBack=function(){
                if(window.__mmAppBackStack.length){window.current=window.__mmAppBackStack.pop();window.drawNav();window.draw();return true;}
                return false;
              };
            })();
        """.trimIndent()
        webView.evaluateJavascript(script, null)
    }

    private fun updateStatusFromMonitorPage() {
        if (::webView.isInitialized && pageReady && Uri.parse(webView.url ?: "").path == "/manage") {
            statusText.text = "Uzaktan yönetim · Yetkili oturum gerekir"
            updatedText.text = "Komut sonuçlarını yönetim ekranından takip edin"
            swipeRefresh.isRefreshing = false
            return
        }

        if (!resumed || !pageReady || isFinishing || isDestroyed) return
        val script = """
            (function(){
              var s=window.state;
              return JSON.stringify({
                pageUpdated:(document.getElementById('updated')||{}).textContent||'',
                generated:s&&s.generated_local||'',
                freshness:s&&s.freshness&&s.freshness.state||'',
                age:s&&s.freshness&&s.freshness.heartbeat_age_seconds,
                source:s&&s.source_state||''
              });
            })()
        """.trimIndent()
        webView.evaluateJavascript(script) { encoded ->
            if (!resumed || isFinishing || isDestroyed) return@evaluateJavascript
            try {
                val jsonString = JSONArray("[$encoded]").getString(0)
                val info = JSONObject(jsonString)
                val pageUpdated = info.optString("pageUpdated")
                if (pageUpdated.contains("Bağlantı yok", ignoreCase = true) || info.optString("source") == "ERROR") {
                    statusText.text = "G62 veri API erişilemiyor"
                    updatedText.text = "Son veri güncellemesi doğrulanmıyor"
                    return@evaluateJavascript
                }
                val freshness = info.optString("freshness")
                val age = if (info.isNull("age")) null else info.optLong("age")
                when {
                    freshness == "CURRENT" -> statusText.text = "G62 erişilebilir · Veri güncel${age?.let { " · ${it} sn" } ?: ""}"
                    freshness.isNotBlank() -> statusText.text = "G62 erişilebilir · Veri gecikmiş"
                    else -> statusText.text = "G62 erişilebilir · Veri yükleniyor"
                }
                val generated = info.optString("generated")
                updatedText.text = if (generated.isNotBlank()) "Son veri: $generated" else "Son veri: $pageUpdated"
                overlay.visibility = View.GONE
            } catch (_: Exception) {
                statusText.text = "G62 erişilebilir · Veri yükleniyor"
            } finally {
                swipeRefresh.isRefreshing = false
            }
        }
    }

    override fun onResume() {
        super.onResume()
        resumed = true
        if (::webView.isInitialized) {
            webView.onResume()
            webView.resumeTimers()
            if (!pageReady) loadMonitor(showLoading = true)
        }
        if (!statusPolling) {
            statusPolling = true
            handler.post(statusPoll)
        }
    }

    override fun onPause() {
        resumed = false
        statusPolling = false
        handler.removeCallbacks(statusPoll)
        handler.removeCallbacks(retryTask)
        retryPending = false
        if (::webView.isInitialized) {
            webView.onPause()
            webView.pauseTimers()
        }
        super.onPause()
    }

    @Deprecated("Android compatibility back handler")
    override fun onBackPressed() {
        if (::webView.isInitialized && pageReady) {
            webView.evaluateJavascript("window.__mmAndroidBack ? window.__mmAndroidBack() : false") { handled ->
                if (handled != "true") {
                    if (webView.canGoBack()) webView.goBack() else finish()
                }
            }
        } else finish()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        try {
            connectivity.unregisterNetworkCallback(networkCallback)
        } catch (_: RuntimeException) {
        }
        if (::webView.isInitialized) {
            webView.stopLoading()
            webView.destroy()
        }
        super.onDestroy()
    }

    private fun rounded(fill: Int, stroke: Int, radius: Int): GradientDrawable = GradientDrawable().apply {
        setColor(fill)
        setStroke(dp(1), stroke)
        cornerRadius = radius.toFloat()
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    companion object {
        private const val STATUS_POLL_MS = 30_000L
    }
}
