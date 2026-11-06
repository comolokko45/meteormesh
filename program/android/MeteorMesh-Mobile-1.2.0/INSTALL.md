# MeteorMesh Mobile 1.2.0 · RC224

Poco F7 Ultra için beş ekranlı Türkçe istasyon uygulaması: Özet, Kameralar, Gece, Veriler ve Yönetim.

## Kurulum

1. Tailscale açık ve mevcut özel ağınıza bağlı olsun.
2. `dist/MeteorMesh-Mobile-1.2.0.apk` dosyasını açıp mevcut MeteorMesh Mobile üzerine güncelle. Eski uygulamayı silmen gerekmez; paket kimliği ve imza korunur.
3. Özet ekranı otomatik açılır. Gerçek adres: https://meteorhub.tail520fb9.ts.net/mobile . Telefonun Tailscale IP'sini APK'ya yazman gerekmez.
4. Yönetim için Yönetim sekmesine gir. Mevcut G62 yönetim anahtarını kullan. Anahtar Windows'ta `%LOCALAPPDATA%\MeteorMeshMobile\management\YONETIM_ANAHTARI.txt` dosyasında; APK içinde veya GitHub'da bulunmaz. Anahtarı başkalarıyla paylaşma.
5. Komut onayında cihazı ve süreyi kontrol et. Sonucu işlem geçmişinden izle. Servisin bitmesi bütün analizlerin veya dosyaların tamamlandığını tek başına kanıtlamaz.

## Yeni ekranlar

- Özet: gerçek MP4 ilerlemesi, uyarı gelen kutusu, analiz, HDD, son aktarım, kapasite tahmini ve paylaşılabilir metin özeti.
- Kameralar: Ruby/Surya/Viva karşılaştırması; HTTP, Control-013 ve bakım amaçlı ADB ayrı. Son MP4, alan ve rezerv; aktarım durursa tahmini kayıt payı.
- Gece: 19:00–05:00 zaman çizelgesi, dokunulabilir segment ayrıntıları, analiz ilerlemesi ve kabul/rapor kanıtları. Gelecek saatler kayıp sayılmaz.
- Veriler: cihaz, analiz durumu, saat/segment arama; 30'luk sayfalama; aktarım, negatif temizlik ve telefon purge fişleri. Adaylar bilimsel meteor kabulü değildir.
- Yönetim: süreli kayıt/mola (15/30/60 dakika), otomatiğe dönüş, durum yenileme, güvenli aktarım ve analiz talebi; sonraki analiz turlarını duraklat/devam ettir. Çalışan analiz kesilmez; ayrı 05:30 başlangıcı korunur. Duraklatma reboot sonrası kalıcı değildir.

Koyu/açık tema, yalın/teknik görünüm, Android geri tuşu, aşağı çekerek yenileme ve uygulama görünürken 30 saniyelik güncelleme desteklenir. Klasik 12 bölüm Yönetim ekranındaki bağlantıdan açılır. Arka planda sürekli veri çekilmez.

## Yetki ve güvenlik

Yalnız doğrulanmış Tailscale HTTPS sunucusu açılır. TLS hataları atlanmaz; cleartext, mixed content, file/content erişimi ve JavaScript native köprüsü kapalıdır. Yeni kamera/dosya/bildirim izni yoktur. Oturum 20 dakikadır; anahtar saklanmaz. Komutlar sabit izin listesi, CSRF, UUID, cooldown ve sonuç fişi kullanır. Eski veya çevrimdışı veride yeni komut kapalıdır. APK dosya silme, keyfi terminal, format veya reboot sunmaz.

## Derleme ve güncelleme

JDK 17, Android SDK 35 / Build Tools 35.0.0 ve Gradle wrapper kullanılır. `Build-Release.ps1` aynı release anahtarıyla APK üretir. Anahtar `%LOCALAPPDATA%\MeteorMeshMobile\signing` altında; kaynak paketine dahil değildir. Sonraki güncellemede applicationId `org.meteormesh.mobile` ve mevcut imza korunmalı, versionCode artırılmalıdır. Bu sürüm versionCode 4'tür.

## Kabul sınırı ve geri dönüş

Derleme, imza, hedefli backend/UI testleri ve gerçek HTTPS durum komutu doğrulandı. 412×915 tarayıcıda mobil ekran, filtre, segment ayrıntısı, tema ve oturumsuz komut kilidi görüldü. Poco F7 Ultra üzerinde yeni APK kurulum/geri tuşu/uyku-ağ dönüşü kabulü kullanıcı kurulumu sonrası bekliyor.

G62'de yalnız monitör RC224'e geçirildi; kamera APK'ları ve analiz süreçleri değiştirilmedi. Sunucu geri dönüşü: `~/.config/systemd/user/meteormesh-monitor.service.d/98-rc224-mobile.conf` dosyasını kaldır, `systemctl --user daemon-reload` ve `systemctl --user restart meteormesh-monitor.service` çalıştır. Önceki RC222 klasik monitör döner. Tam geri dönüş sırasında yeni `/mobile` sayfası kullanılamaz; klasik HTTPS kök adresi tarayıcıda kullanılabilir. Android eski versionCode'un üstüne downgrade kurulmaz; verileri silmeden geri dönüş için aynı anahtarla daha yüksek versionCode'lu önceki kaynak derlenmelidir.
