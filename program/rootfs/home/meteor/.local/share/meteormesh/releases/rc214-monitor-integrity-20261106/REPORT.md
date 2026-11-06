# MeteorMesh RC214 ve Mobile 1.0.1 — Kod inceleme ve düzeltme raporu

Tarih: 6 Kasım 2026. Canlı dağıtım kanıtı: 2026-11-06T12:35:28+03:00.

## Yüzde hesabının düzeltilmesi

Önceki %87,5/%12,5 hesabı 8 kabul kapısının ağırlıksız puanıdır. Kalan iki PARTIAL kapısı mobil ekran/yenileme kabulü ve güncel SMART ölçümüdür. Bu nedenle %12,5'i kalan kod miktarı veya iş süresi diye yorumlamak yanlıştı. Bu tur puanı yükseltmek için kapılar PASS yapılmadı. Arayüze de bu ölçüm sınırı eklendi.

## Kaynakta bulunan ve giderilen kusurlar

1. Kuyruk verisi yokken sıfır sayılması: RC213 qcount geçersiz veya eksik alanları sıfıra çevirebiliyordu. RC214 READ_OK, doğru gece, tüm zorunlu sayaçların negatif olmayan tamsayı olması koşulunu arar. Bilinmeyen dolu iş durumunda kapanış BLOCKED olur. build_state da okunamayan kuyruğu QUEUE_UNAVAILABLE gösterir. Hedefli kontroller PASS.
2. Tarihsel başarıların yeni başlangıç sayılması: canlı SUCCEEDED sayısı, zaman damgalı analyzer kanıtı yokken 05:30 PASS üretebiliyordu. Bu yedek koşul kaldırıldı. Gerçek gece ve zamanla eşleşen analyzer başlangıcı gerekir. Hedefli kontrol PASS.
3. Tarihsel kaynak eksiklerinin yeni geceyi bloke etmesi: carryover WAITING=99, yanlışlıkla güncel gece kapanışına zorunlu engel oluyordu. RC214 yalnız doğru gecenin kuyruğu ve tamamlanmış detector raporunu kullanır; eski işler veritabanında ve raporda korunur. Güncel WAITING/FAILED/işsiz segment hâlâ kapanışı engeller. Hedefli kontroller PASS.
4. Mobil web görünümü: HTTP hata kodu/yanlış JSON kontrolü, 20 saniyelik timeout, tek etkin istek, arka planda polling durdurma eklendi. API kesildiğinde son veri tutulur ve sekme değiştirilse de bağlantı kesik uyarısı korunur. localStorage reddi sayfanın açılışını durdurmaz. Dokuz JavaScript senaryosu PASS.
5. Android görüntüleme motoru kaybı: eski APK onRenderProcessGone sonrasında yok olmuş WebView nesnesini tekrar kullanıyordu. Mobile 1.0.1 eski view'u kaldırıp destroy eder, yeni view kurar, aynı güvenlik ayarlarını uygular ve sınırlı retry akışına döner. Derleme/lint PASS; gerçek cihaz çökme/kurtarma kabulü PARTIAL.
6. Android izinli adres kontrolü: yalnız aynı host kontrolüne ek olarak varsayılan HTTPS/443 ve userInfo yok koşulları eklendi; navigasyon ve kaynak istekleri aynı kontrolü kullanır. Arka plana geçişten sonra gecikmiş durum callback'leri arayüzü güncellemez. Derleme/lint PASS; cihaz kabulü PARTIAL.

## Uygulama ve kanıt

RC214 yalnız G62 monitor servisine uygulandı. Servis healthz=ok; API kaynağı RC214. Capture/analyzer yeniden başlatılmadı. 09:00 timer disabled kaldı. SQLite veya MP4 değiştirilmedi.
Canlı kapsam 20261105-night: 217 SUCCEEDED, 142 WAITING, 1 RUNNING, 0 FAILED. 05:30 başlangıç PASS; kuyruk bitmediği için analysis_drain PARTIAL. Açık yerel uyarı STORAGE_LOW:surya; tarihsel 119 FAILED ayrıca gösteriliyor.
11 izole Python gate senaryosu ve 9 JavaScript yenileme senaryosu geçti. Bunlar yalnız değişen davranışları kapsar; gerçek Android ekran/kamera veya tüm gece testi değildir.
Mobile 1.0.1 versionCode=2, org.meteormesh.mobile; assembleRelease, lintVitalRelease ve APK v2 imza doğrulaması başarılı. Önceki APK ile aynı sertifika kullanıldı. Nihai APK SHA-256: f320acc91cde54210ec2542c394bf071537b08d348c2e0522ee09da5c7d8e71b.
İlk hazırlıkta Windows metin kodlaması hatası nedeniyle kaynak yaması durdu. UTF-8 düzeltilip kaynak değişiklikleri uygulandıktan sonra nihai APK yeniden derlendi; yukarıdaki hash yalnız bu son çıktıya aittir.

## Geri dönüş

G62 önceki RC213 release korunuyor. RC214 dönüşü: yalnız ~/.config/systemd/user/meteormesh-monitor.service.d/92-rc214-integrity.conf dosyasını kaldır; systemctl --user daemon-reload; systemctl --user restart meteormesh-monitor.service. Dağıtım betiği açılış sağlık/API kontrolü başarısızsa bunu kendisi yapar. Manifest ve live-evidence.json bu pakettedir.
Android 1.0.0 kaynak ve APK korunuyor. Mevcut telefon kurulumu değiştirilmedi. Android normalde düşük versionCode kurulumunu kabul etmeyebilir; veri koruyan geri dönüş gerekirse eski kaynağı daha yüksek versionCode ile aynı anahtarla derle. Otomatik uninstall veya veri silme uygulanmadı.

## Gerçekten kalan işler

- Mobile 1.0.1'in F7 Ultra üzerinde kurulumu, 12 ekran, bağlantı kesilip geri gelmesi ve WebView kurtarma kabulü: PARTIAL. APK hazır; bu oturumda telefona kurulmadı.
- SMART: /srv/meteor-data kaynağı /dev/sdb1, smartctl kurulu. sudo -n -l parola istedi. Canlı SMART değerleri alınamadı; yönetici erişimine bağlı BLOCKED. Önceki 446 sektör sayısı güncel ölçüm değildir.
- Devam eden analiz kuyruğunun doğal kapanışı ve 7 Kasım 05:30 RC211 davranışı: PARTIAL, zamanına bağlı.
- Tam gece PTS/UTC sınır kanıtı, çok geceli güvenilirlik, kontrollü reboot ve gerçek .part resume: bu turda kabul edilmedi.
- Dış push teslimi: yapılandırılmadı; OpsJournal yerel kayıt üretir.

RC214 bu eksikleri tamamlanmış gibi göstermez. Bu tur tüm MeteorNode/MetDetPy kaynaklarının eksiksiz denetimi değildir; kalan puanın dayandığı monitör/mobil ve analiz kabul kodu incelendi.
