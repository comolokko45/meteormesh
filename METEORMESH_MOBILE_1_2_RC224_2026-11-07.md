# MeteorMesh Mobile 1.2.0 / RC224 — son geliştirme raporu

Tarih: 7 Kasım 2026. Amaç: mevcut G62 üzerinde daha sade ve yetkili mobil istasyon arayüzü. Gece kayıtları kesilmedi.

## Uygulanan geliştirmeler — PASS

Beş mobil ekran; koyu/açık tema; dokunulabilir gece çizelgesi; cihaz/durum/saat filtreli veri masası; detay çekmeceleri; uyarı gelen kutusu; kapasite tahmini; kopyalanabilir özet; çevrimdışı son bilinen durum; 30 sn görünür güncelleme; yalın/teknik görünüm; mevcut 12 bölümlü monitöre geçiş. Veri API'si ve iş kuyruğu çoğaltılmadı.

Mevcut yönetim protokolüne 15/30/60 dk tercih eklendi. Eski istemcinin süresiz parametresi 60 dk olarak uyumludur. Süre bool/string/izin dışı sayıysa reddedilir; aynı UUID farklı süreyle tekrar kullanılamaz. Kamera kayıt zamanlayıcısı aynı intent/lock dosyasını okur. Yeni kamera servisi kurulmadı.

İki sabit işlemle yalnız mevcut `meteormesh-analysis-rounds.timer` start/stop edilir. Çalışan analyzer kesilmez; 05:30 ayrı başlangıcı ve enabled durumu korunur. Uygulama süre ve etkiyi gönderimden önce açıklar. Bu yetkiler oturum, CSRF, cooldown, UUID ve fiş kurallarına tabidir.

## Hedefli doğrulama

| Kontrol | Sonuç | Kanıt/sınır |
|---|---|---|
| Kotlin release derleme, lintVital, aynı imza | PASS | versionCode4; APK üretildi |
| UI null/bozuk alan, escaping, gecikmiş MP4, bilinmeyen kapasite, offline komut kilidi | PASS | test_ui.js; Node VM |
| Süre şeması, UUID uyuşmazlığı, sınırlı kamera dispatch, sabit timer komutu | PASS | test_backend.py; geçici dizin ve mock, gerçek kayıt/timer değiştirilmedi |
| Gerçek HTTPS /mobile, CSS, JS, API | PASS | Özel Tailscale Serve; loopback8765 korunuyor |
| Auth olmadan yönetim reddi, giriş, durum talebi ve tekrarda aynı UUID | PASS | 544fd6ca-145f-4e10-8fdd-be3d9704907b; SERVICE_COMPLETED; InvocationID71f066cc12a2406795a41e77a0e5f4ad |
| Mobil tarayıcı 412×915 | PASS | 5 sekme; Surya00:30 araması1 sonuç; segment çekmecesi; açık/koyu tema; oturumsuz komutlar kapalı; konsol hata0 |
| Yeni süreli kamera ve timer komutunun gerçek üretim etkisi | PARTIAL | Backend hedefli test geçti; aktif geceyi korumak için gerçek stop/start gönderilmedi |
| F7 Ultra yeni APK gerçek kurulum | PARTIAL | Yeni paket teslim edildi; telefona kurulmadı |

Canlı kontrolde Ruby/Surya/Viva3/3 recording, HTTP/ControlONLINE; 204 doğrulanmış aktarım ve204 WAITING gece işi görüldü. Surya33,4GiB, rezerv32GiB; uyarı görünür. Bu gece05:30 henüz gelmedi: yeni gecenin analizi tamamlandı veya meteor yok denmedi. Kaynak çıkışı oluşmamış aday sayısı `—` gösterilir. 289 tarihsel negatif temizleme fişi55,6GiB raporlanır; bu çalışma veri silmedi.

## Kök nedenler ve değişen dosyalar

Önceki APK masaüstü ağırlıklı monitör ve ayrı yönetim sayfası kullanıyordu; çift başlık ekran alanını daraltıyordu. Yeni `/mobile`, `mobile.html/css/js` aynı API'yi mobil düzende kullanır. `manage_server.py` statik rotaları, süre şemasını ve sabit timer izin listesini genişletir. `capture_control.py` aynı intent'e seçilen süreyi yazar. Android `MainActivity.kt` yeni sayfayı açar, çift başlığı kaldırır ve mobil geri gezinme kancasını kullanır. `app/build.gradle.kts`, Build-Release.ps1 ve kurulum belgeleri sürümlendi.

## Etkin sürümler / korunmuş sınırlar

Monitör RC224; OpsJournal/AutoPilot politikası RC223 SHADOW; mevcut timer/listener kurtarması korunur. Kamera MeteorNode0.13.6; önceden hazırlanmış0.13.7-security1 bu görevde kurulmadı. Tailscale HTTPS adresi https://meteorhub.tail520fb9.ts.net/mobile . Mevcut anahtar/sertifika/SSH/Tailscale bağlantıları değişmedi; Funnel yok. Ağ erişimi artmadı; ek yönetim işlemleri mevcut yetkili protokolün sabit izin listesinde.

Eksik bilgiler varmış gibi üretilmedi: aday başına klip/inceleme detayı, ham segment SHA'sı ve güncel SMART ölçümü bu mobil API'de yok. Klasik monitör bağlantısı korunur. APK içi uyarı ve yerel günlük vardır; dış push yok. Yeni keyfi komut, toplu silme, root, reboot veya Android izin atlama eklenmedi.

## APK ve teslim

APK: MeteorMesh-Mobile-1.2.0.apk
SHA-256: `6e2413ecf21185f0c1e01db457b978cf04aeedf40bd2bbf37691f342f99419b1`
İmza sertifikası SHA-256: `9923b5b17ef990bb12e20f9a727ba32e571fc20d2a8912dc3a29ca04f9eacdc1`

Kaynak paketi ayrı ZIP'te; anahtar, yönetim parolası, local.properties ve build/cache dizinleri yok. Sunucu sürümü /home/meteor/.local/share/meteormesh/releases/rc224-mobile-experience-20261107 . Önceki ExecStart ve dosya hashleri manifest.json'da. Yalnız monitör restart edildi; capture/analyzer durdurulmadı.

Rollback:98-rc224-mobile.conf kaldırılıp user daemon-reload ve yalnız monitör restart edilir; önceki RC222 geri gelir. APK1.2 için /mobile rotası RC224 ister; geri dönüşte klasik kök HTTPS adresini tarayıcıdan kullan. Kamera intent dosyası geriye uyumludur. Yeni APK için gerçek cihaz kabulü bitmeden FINAL veya yeni tamamlanma yüzdesi verilmez.
