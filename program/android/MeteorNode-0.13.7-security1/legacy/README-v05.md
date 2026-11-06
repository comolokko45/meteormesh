# MeteorNode 0.5 — derlenmiş ve Android Lint denetiminden geçmiş alfa

**Durum:** 0.4 üzerine güvenlik/dayanıklılık düzeltmeleri. Android SDK 35, Gradle 8.9 ve JDK 17 ile **gerçek debug APK derlemesi** ve APK statik doğrulaması yapılır. Debug imzası test içindir; mağaza/üretim imzası değildir. Üç telefonda gerçek kamera/TLS/8 saat testi yapılmadı. İlk kamera `ruby`, ardından `viva` ve `surya`.

## 0.5 düzeltmeleri

- Android Lint'in bulduğu `MissingPermission` hatası giderildi: Kamera izni başlatma isteğinden sonra geri alınmışsa `openCamera` çağrısından hemen önce yeniden kontrol edilir; orijinal dosyalar korunarak kayıt durur.
- Android 12+ yedekleme/cihaz aktarımı ve Android 11− yedekleme kurallarında tüm özel veriler (`segments` ve eşleşme anahtarı dahil) açıkça hariç tutuldu. `allowBackup=false` korundu.
- İlk çalıştırmada ana ekran ile TLS sunucusunun aynı anda eşleşme anahtarı oluşturması önlendi; tek kalıcı anahtar kullanılır.
- APK'nın cihaz sistem ikonuna bağımlı başlatıcı simgesi yerine kendi vektör simgesi eklendi; paket manifesti bağımsız incelenebilir.
- `minSdk=26` olduğundan gereksiz eski Android kod yolu kaldırıldı; debug sürümü, UI, manifest etiketi ve sağlık API değeri 0.5 olarak eşlendi.
- 0.4 sürümünün real APK derlemesi başarıyla yapıldı ancak Lint 1 hata, 11 uyarı bildirdi; 0.5'te engelleyici hata onarıldı. Kalan uyarılar için ayrıntılı build raporuna bakın.

## 0.4 çift denetim düzeltmeleri

- Camera2 hiç FPS aralığı bildirmiyorsa artık desteklenmeyen 25 FPS varsayılmaz; güvenli hata üretilir.
- Ana ekran, Android manifest etiketi, `versionCode=4` / `versionName=0.4.0-alpha4` ve LAN `node_version=0.4` eşleştirildi.
- Android kaynak sözleşmesi denetimi 6/6; Linux indirme protokolü denetimi 15/15 geçer. Hub ile aynı disk rezervi kontrolü ayrı Linux indirme aracına da eklendi. **APK derlemesi veya gerçek kamera testi değildir.**

## 0.3'te gerçekten değişenler

1. `SegmentStore` artık uygulama bağlamında **tekil** yaratılır; `MainActivity` ile `MeteorCaptureService` aynı indeksleme/finalizasyon kuyruğunu paylaşır. Durum ekranına her basışın yeni daemon işçisi üretmesi ve olası `.json.tmp` yarışı engellendi.
2. `listReady()` dışarıya gösterdiği her metadata kaydındaki `segment_id` değerini dosya yolunda kullanmadan önce güvenli karakter kümesine göre doğrular. Boyut, durum ve kaynak dosya kontrolü korunur. Linux tarafı SHA-256 doğrulaması yine zorunludur.
3. Android sürüm numarası, ana ekran başlığı ve uygulama etiketi `0.3` olarak eşlendi (`versionCode=3`).
4. Beş çevrimdışı Android **kaynak sözleşmesi** testi ve 14 Linux indirme protokolü testi başarıyla çalıştı (**19/19**). Bu kontroller Kotlin/Android derlemesi değildir; Android çalışabilirliğini kanıtlamaz.
5. 0.2'deki otomatik silme kapalı politikası, 300 saniyelik `MediaRecorder` stop/restart ve açık timestamp belirsizliği **aynen korundu**.

## 0.2'de düzeltilenler

- `SegmentStore`: Başarıyla durmuş `.recording` dosyasının SHA-256 ve `.json.tmp` metadata'sı önce diske yazılır; sonra MP4 ve JSON hazır hale getirilir. Süreç ortada kapanırsa sonraki açılışta **yalnız geçerli staged metadata ve eşleşen hash** ile onarılır. Belgesi eksik/eski dosyalar kanıtsız READY yapılmaz, silinmez.
- `/api/v1/health`: `unindexed_files_retained` eklendi. Aktif kayıt dosyası da bu sayıya dahildir; hata sayacıyla aynı şey değildir.
- `MeteorCaptureService`: Açılış ve session hatalarında sınırlı yeniden deneme; EIS/OIS destekleniyorsa OFF başlangıç isteği.
- Telefon depolaması 2 GiB altına indiğinde kayıt güvenli biçimde durur; **otomatik silme hâlâ yoktur**.
- `linux/meteornode_pull.py`: Metadata ya da video taşıma aşaması yarıda kalırsa idempotent toparlama; farklı dosyanın üzerine yazmama. MeteorHub 0.5 ile aynı indirme protokolü.

**Kritik sınır değişmedi:** 0.5 hâlâ `MediaRecorder.stop()` / `start()` ile yaklaşık 300 saniyelik segmentler üretir; segmentler arası gerçek kare kaybı olabilir. Kesintisiz `Camera2 → MediaCodec` motoru, PTS↔UTC ölçümü, reboot sonrası başlatma, 8 saat kayıt garantisi henüz yoktur.

## Bu sürüm neleri yapar?

- Kamera2 HAL kamera/video çözünürlüğü, `MANUAL_SENSOR`, ISO aralığı, pozlama aralığı, FPS ve sensor timestamp kaynağını raporlar.
- Kullanıcı uygulama görünürken kaydı başlatır; foreground camera service ve wakelock ile ekran kapalıyken kayıt devam etmeyi dener.
- Otomatik veya manuel ISO 800/1600 (desteklenirse) profilini kullanır. Kamera odak kontrolü varsa sonsuza sabitlemeyi dener.
- H.264/MP4 dosyalarını yaklaşık **300 saniyelik** parçalara böler. **Önemli: Bu alpha sürümde `MediaRecorder` durdurulup yeniden başlatıldığı için birkaç kare veya saniye kaybolabilir.** Böyle bir durum henüz ölçülmedi; **kesintisiz/gapless bilimsel kamera diye değerlendirmeyin.**
- Dosya tamamlanınca SHA-256 hesaplar, yanında metadata JSON üretir. Tamamlanmamış `.recording` dosyaları uzak listede görünmez.
- Telefonda read-only HTTPS + Bearer pairing ile `/api/v1/health`, `/api/v1/capabilities`, `/api/v1/segments`, `/api/v1/segments/{id}/meta` ve **HTTP Range/206** `/api/v1/segments/{id}/file` sunar.
- Linux Python istemcisi TLS sertifikasını telefondaki SHA-256 parmak izine sabitler; **kimlik anahtarını TLS sertifikasını kontrol etmeden göndermez**; indirilen MP4'ün SHA-256 değerini ayrıca doğrular. Kesilen aktarımlara `.part` üzerinden kaldığı yerden devam eder.
- **Veri silme uygulanmadı.** `/api/v1/retention` yalnız `dry-run/auto_delete=false` döndürür. Silme reçetesi/protokolü proje v1.2 planında ve sonraki sürümün ayrı bir test maddesidir.

## Bu sürüm neleri henüz yapmaz?

- Üç telefon arasında doğrulanmış gerçek UTC frame senkronizasyonu ve video PTS↔sensor eşleştirmesi (timestamp metadata şu an **ilk/son exposure ve tahmini UTC** düzeyindedir).
- Gecelik zamanlama; Android 14/15/16 reboot sonrası kendi kendine arka plandan kamera açma garantisi yoktur.
- Gerçek yıldız-kamera kalibrasyonu, MetDetPy, RMS/SkyFit2, meteor tespiti, Linux web paneli, Drive arşivi, MCP/AI agent.
- Geçerli staged metadata'sı olmayan eksik/bozuk MP4'ü otomatik kurtarma; dosya yerinde inceleme için tutulur. Otomatik sıcaklık bazlı FPS ayarlama, dayanıklılık ve termal validasyon.
- Sıfır kare kaybı veya 8 saat kesintisiz çalışma garantisi.

## Android derleme

Gerekenler: JDK 17, Android SDK Platform 35, Build Tools 35 veya AGP'nin uyumlu sürümü, Gradle 8.9. Bu proje AGP 8.7.3 ve Kotlin 2.0.21 kullanır.

Android Studio ile bu klasörü açıp `app` için **Build APK(s)** çalıştırın. Komut satırında Gradle yüklüyse:

```bash
gradle :app:assembleDebug
```

Çıktı: `app/build/outputs/apk/debug/app-debug.apk`. Debug APK kurulumu için Android'de bilinmeyen kaynak izni / USB debugging gerektirebilir; telefonun açık izni olmadan kurulum yapılamaz.

## Telefon testi

1. MeteorNode'u kurun, **Kamera** ve Android 13+ ise **Bildirim** iznini verin. Cihazın pil optimizasyonunda MeteorNode'a uzun çalışma izni verin (firmware davranışı farklı olabilir).
2. İlk 10 dakikalık gündüz denemesinde **Auto 1080p** profilini seçin, **Kaydı Başlat** düğmesine basın.
3. Ekranı kapatın; 10–12 dakika sonra tekrar açıp durum ve hazır kayıt sayısını yenileyin.
4. İlk kaydın iki parça halinde ve telefondaki uygulama depolamasında oluştuğunu doğrulayın. `last_error` değerini ve video dosyalarını not edin.
5. Açık havada gece yıldız çekiminde manuel profillerin gerçekten çalışıp çalışmadığını ayrı ayrı test edin; ISO isteği HAL tarafından yok sayılabilir. Lens sonsuz odak ayarını yıldız netliğiyle doğrulayın.
6. 8 saatlik testten önce şarj/pil/ısı ve yağmur/çiğ güvenliğini sağlayın. Uzun kayıt sırasında şarjı sürekli %100'de bırakmayın.

## Linux'tan güvenli dosya listeleme / indirme

Telefonun aynı Wi-Fi ağındaki **IP adresini**, APK ana ekranındaki uzun **pairing token** değerini ve **TLS sertifika SHA-256 parmak izini** okuyun. Bu bilgileri internette yayınlamayın.

Token'ı shell geçmişine yazmamak için:

```bash
read -rs -p "MeteorNode token: " METEORNODE_PAIRING_TOKEN
export METEORNODE_PAIRING_TOKEN
```

Ardından Linux bilgisayarda:

```bash
python3 linux/meteornode_pull.py \
    --host 192.168.1.50 \
    --fingerprint "TELEFON_EKRANINDAKI_SHA256" \
    --destination ./ruby-archive \
    --list
```

`--list` kaldırıldığında MP4 ve metadata dosyalarını belirtilen dizine indirir, kesilen işlemleri sürdürür ve SHA-256'yi karşılaştırır. Telefonun IP'sini kendi ağınızdan kullanın; `192.168.1.50` yalnız örnektir.

**Güvenlik:** Servis yalnız HTTPS sunar ve token ister. Sertifika ilk açılışta AndroidKeyStore'da oluşturulur; standart CA sertifikası olmadığı için Linux istemci telefon ekranındaki fingerprint'i **mutlaka** doğru ve güvenilir kanaldan almalıdır. TLS/AndroidKeyStore özellikleri gerçek üç cihazda henüz doğrulanmamıştır. G62'ye/Android'e internetten port yönlendirmesi yapmayın. V0.5'te uzaktan kayıt başlat/durdur veya silme API'si **yoktur**.

## Linux sürücüsü testi

```bash
python3 -m unittest discover -s linux -v
```

Bu 15 test yalnız Linux protokolünün Python tarafını doğrular. Ek olarak `python3 -m unittest discover -s . -p 'test_android_source_contract.py' -v` dokuz Android kaynak sözleşmesi kontrolü yapar. **Her ikisi de gerçek telefon testi değildir.** Kamera/Android/TLS socket ve 300 saniyelik bölme davranışı gerçek cihazda test edilmelidir.

## Proje dosyaları

```text
app/src/main/java/org/meteormesh/node/
  MainActivity.kt           Basit kamera ayarı/başlat/durdur ekranı
  CameraInventory.kt       Camera2 yetenek taraması
  MeteorCaptureService.kt  Foreground camera service, MP4 segment
  SegmentStore.kt          Metadata ve SHA256 ile hazır dosya indeksi
  LocalTlsServer.kt        Read-only eşleşmeli HTTPS/Range dosya API
linux/
  meteornode_pull.py       Linux TLS pinned ve resumable MP4 indirici
  test_meteornode_pull.py  İlk birim testleri
test_android_source_contract.py  Salt-okunur Android kaynak güvenlik denetimi
```

**Sonraki iş:** Önce `ruby` modelinde derleme/yükleme ve 10 dakikalık MP4 testi; sonra bölme aralıklarındaki gap ölçümü ve `MediaCodec/MediaMuxer` veya güvenilir rollover ile geliştirme; daha sonra viva/surya, Linux MetDetPy ve doğrulanmış saklama makbuzuyla otomatik temizleme.
