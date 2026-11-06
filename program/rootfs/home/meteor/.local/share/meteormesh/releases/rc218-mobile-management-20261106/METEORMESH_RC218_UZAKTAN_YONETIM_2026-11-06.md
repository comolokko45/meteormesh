# MeteorMesh Mobile 1.1.0 — uzaktan yönetim teslimi

6 Kasım 2026, 19:35 TRT. G62 üretim sürümü `rc218-mobile-management-20261106`.

MeteorMesh Mobile 1.1.0, mevcut Tailscale Serve HTTPS adresindeki monitöre **Yönet** düğmesini ekler. Sayfa yalnızca G62 üzerinde `127.0.0.1:8765` dinler. Tailscale Serve ayarı değiştirilmedi; Funnel kullanılmadı. Yönetim için G62'nin ürettiği ayrı rastgele anahtar gerekir; anahtar APK'ya gömülmez. Anahtarın SHA-256 özeti G62'de kullanıcıya özel mod 0600 dosyada tutulur. İmzalama anahtarı eski 1.0.1 ile aynıdır, versionCode 3.

## Kullanılabilir komutlar

- G62 durum bilgisini yenileme.
- Mevcut saatlik aktarım servisine yeni çalışma talebi gönderme. Servis meşgulse veya kendi kapıları izin vermezse talep bunu belirtir.
- Mevcut analiz servisine tur talebi gönderme. NightScope, analiz saati, disk rezervi ve exclusive lock kapıları değişmedi. `SERVICE_COMPLETED` tek başına bütün videoların analiz edildiği anlamına gelmez.
- Mevcut kamera kontrol servisine güvenli kontrol talebi gönderme.
- Ruby, Surya veya Viva için bir saatlik kayıt başlatma ya da bir saatlik kayıt molası. Süre sonunda 19:00–05:00 otomatik programına dönülür. Depolama eşikleri 32/33 GiB korunur. Android kamera hazır değilse başarı yerine `NEEDS_REARM_OR_STORAGE` döner. Mola bilinçli kayıt boşluğu oluşturur.
- Seçili cihazı otomatik kayıt programına geri alma.

Komutlar açık kullanıcı onayı, 20 dakika süreli oturum, aynı kaynak kontrolü, CSRF, işlem kimliği, 60 saniye tekrar sınırı ve kalıcı işlem fişiyle korunur. Monitörün mevcut salt okunur API'si çalışmaya devam eder. Kamera kontrolü yeni yetkiyle de mevcut MeteorNode API'sini ve mevcut yakalama mantığını kullanır. Kullanıcı kayıt başlatırsa kaynak video 32/33 GiB eşiğiyle korunur. Kilit aynı anda manuel ve otomatik kamera kararını engeller.

Genel terminal, keyfi dosya silme, APK dağıtımı, yeniden başlatma, Wi-Fi değişimi ve sistem yapılandırma komutları bu sürümün komut listesinde yoktur. Control-013 yalnız mevcut `restart_listener` komutunu destekler; genişletilmedi. Bu özellikler için ayrı, sınırları ve sonuç doğrulaması tanımlı işlemler gerekir. “Her şeye müdahale” bu sürümde tamamlandı diye gösterilmez.

## Doğrulama

- Yönetim uç noktası yetkisiz GET için 401; yanlış Origin, yanlış CSRF, izin dışı eylem, onaysız ve yinelenen komut reddi hedefli testlerde PASS.
- Gerçek Tailscale HTTPS ve TLS ile giriş, yeni bir durum servisi isteği, yinelenen kimliğin tekrar servis başlatmaması, servisin `SERVICE_COMPLETED` sonucu PASS. Kamera komutu ve veri silme gönderilmedi.
- Kamera sınır testleri: finalizing durumunu durdu saymama, niyetin süre dolumu, çevrimdışıyken otomatiğe dönüşün kaydı ve kamerası hazır değilken başarı verilmemesi PASS.
- APK `assembleRelease`, `lintVitalRelease`, `apksigner verify` PASS. APK SHA-256: `7ce82874eedaa1d6fcd0a861732147b3f0cb35e579c8c12c680a79376684ee80`.
- 19:35 TRT doğal capture tick'i RC218 sarmalayıcısı ile üç cihaz `RECORDING_OWNED`, servis PASS. Canlı APK ekranında kullanıcı kurulumu ve kamera eylemi kabulü henüz yapılmadı.
- G62 monitor `healthz=ok`, yönetim sayfası HTTP 200, mevcut `/api/state` erişilebilir. Analiz ve kayıt prosesleri dağıtım sırasında durdurulmadı.

## Kurulum

1. [MeteorMesh-Mobile-1.1.0.apk](MeteorMesh-Mobile-1.1.0/dist/MeteorMesh-Mobile-1.1.0.apk) dosyasını F7 Ultra'ya yükle. Mevcut 1.0.1 üzerine aynı imzayla güncellenebilir.
2. Tailscale açıkken uygulamada **Yönet** düğmesine gir.
3. Anahtarı Windows'taki `%LOCALAPPDATA%\MeteorMeshMobile\management\YONETIM_ANAHTARI.txt` dosyasından kopyala. Bu dosyayı paylaşma. Oturum 20 dakika sonra kapanır.
4. İşlem gönderdikten sonra sonucu İşlem Sonuçları ve Bilgi Monitörü'nden doğrula. `ACCEPTED` yalnız kabul edilen talep demektir.

## Rollback

G62'de yalnız `~/.config/systemd/user/meteormesh-monitor.service.d/95-rc218-management.conf` ve `~/.config/systemd/user/meteormesh-capture.service.d/~rc218-manual-intent.conf` kaldırılır; `systemctl --user daemon-reload`, ardından yalnız `meteormesh-monitor.service` yeniden başlatılır. RC217 monitörü ve orijinal kamera servisi geri gelir. Mevcut kayıt sırasında capture servisinin kendisi yeniden başlatılmaz. Telefon uygulamasında 1.0.1'e uygulama verilerini silmeden sürüm düşürme Android tarafından engellenebilir; normal geri dönüş için 1.0.1 APK kurulumundan önce 1.1.0 kaldırılması gerekir, bu durumda mobil uygulama yerel verisi silinir ama G62/MeteorNode verisi etkilenmez. G62 anahtarı saklanır veya yönetim kapatılacaksa ayrı kaldırılır.

Kaynak: `MeteorMesh-RC218-Remote-Management-2026-11-06/` ve `MeteorMesh-Mobile-1.1.0/`. G62 sürüm manifesti önceki monitör yolunu ve yayın dosyalarının SHA değerlerini içerir. Anahtar ve imzalama dosyaları kaynak paketine dahil edilmez.
