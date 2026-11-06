# MeteorMesh RC222 — mevcut AutoPilot karar ve doğrulama katmanı

6 Kasım 2026, 22:16 TRT. Kullanıcı bildirim tercihi: yalnız APK içi ve yerel günlük.

## Uygulananlar

1. Mevcut OpsJournal'a salt veri üzerinde çalışan karar modülü eklendi. Yeni agent, daemon, timer veya veritabanı yok. Karar ve doğrulama durumu mevcut `ops-alert-state-v1.json` içindeki küçük `autopilot` alanında tutulur. Doğal ilk turda dosya1578 bayt; eski8192 bayt güvenlik limiti korunur.
2. Her cihazın güncel depolama kanıtı ayrı kontrol edilir. Taze örnek yoksa karar BLOCKED. Düşük alanda çalışan aktarım veya etkin beş dakikalık timer varsa karar DELEGATED; ikinci işlem başlatılmaz. Timer hazır değilse yalnız SHADOW_RETRY önerisi kaydedilir.
3. Alan doğrulaması aynı gece içinde ilerleyen cihaz purge fişi, farklı örnek zamanı ve gerçek boş alan farkını birlikte arar. Alan artışı yalnız gözlem olarak adlandırılır; kesin neden iddiası verilmez. Purge görülüp alan artmaması ayrı sonuçtur. Kaynak olmadan veya eski örnekle toparlandı denmez.
4. Analiz kararı gece kuyruğunu kullanır. Kapsamlı kuyruk bilgisi yoksa BLOCKED; çalışma saati dışında EXPECTED_WAIT; çalışan iş varsa mevcut işçi; başarısız iş varsa inceleme;30 dakika değişmeyen kuyrukta gölge yeniden deneme önerisi. Tarihsel119 FAILED ve99 kaynak-eksik iş bu kuyruğa eklenmez.
5. Kamera hazırlama veya kayıt durması, kör restart yerine NEEDS_REARM/NEEDS_REVIEW üretir. HTTP kesintisi mevcut Control-013 listener kurtarma kapılarına devredilir. Mevcut gerçek listener yolu15 dakika cooldown ve saatte2 deneme sınırını korur. Yeni öneri şemasında da2 deneme/900 saniye sınırı tanımlı; bunlar yeni bir yürütücünün devreye alındığı anlamına gelmez.
6. Karar değişimleri fingerprint ile birleştirilir; aynı karar her turda yeni bildirim gibi yazılmaz. İstek veya öneri başarı sayılmaz. Mevcut gerçek listener HTTP sonrası gözlemi, aktarım SHA/fişleri ve SQLite sonuçları ayrı kanıtlar olmaya devam eder.
7. Mevcut monitörün AutoPilot bölümü kararları, gözlem zamanını, depolama karşılaştırmasını ve son düzelmeleri gösterir. Aynı sunucu sayfasını açan Mobile1.1.0 bu görünümü yeniden APK kurmadan alır. Dış hesap/push kurulmadı; kullanıcı özellikle APK içi ve yerel günlüğü seçti.

## Kanıt

Hedefli senaryolar PASS: timer sahipliği, gece analiz beklemesi, eski telefon örneğini reddetme, purge+alan artışı eşleşmesi, uygunsuz timerda gölge öneri, kuyruk verisi yokken fail-closed, karar tekrarını bastırma ve yan etki olmaması. Olay geçiş regresyonları PASS. Her iki HTML JavaScript'i sözdizimi kontrolünden geçti.

22:16 doğal status turu başarıyla bitti. Canlı kararlar Surya=DELEGATED/FIVE_MINUTE_TRANSFER_OWNS_RETRY, analysis=EXPECTED_WAIT/OUTSIDE_ANALYSIS_WINDOW. Yeni kod hiçbir telefon/analiz/silme komutu göndermedi. API aynı SHADOW kararlarını gösterdi. Kayıt ve analiz servisleri restart edilmedi; yalnız monitör yeni görünüm için restart edildi.

## Bilerek henüz tamamlanmış sayılmayan aşama

Yeni kurallar önce gözlem kaydı üretir. `shadow_started_utc` ve ilk gece kimliği tutulur. Başlangıçtan sonraki doğal sabah ve aynı gecenin tam analiz sonucu görülünce değerlendirme `NATURAL_NIGHT_AND_MORNING_OBSERVED_REVIEW_READY` olur. Kod bu durumda bile yeni müdahaleleri otomatik açmaz. İlk gecenin gerçek sonuçlarına göre uygun kurallar tek tek yürütücüye bağlanmalıdır. Bu koşul henüz oluşmadı; yeni tam yetkili otonom agent ilan edilmiyor. Mevcut capture/transfer/analiz zamanlayıcıları ve sınırlı listener kurtarması etkin kalır.

Gerçek Android APK ekran görüntüsü ve doğal gece sonrası alan-kurtarma doğrulaması bu turda tamamlanmadı. API/günlük üretim kabulü yapıldı. Yeni kararlar kaynak dosya silme veya Android'i zorla uyandırma yetkisi getirmez.

## Yayın ve rollback

G62: `~/.local/share/meteormesh/releases/rc222-autopilot-policy-20261106/`.

- `autopilot_policy.py`: saf karar ve gözlem doğrulaması.
- `meteor-ops-local-alerts.py`: mevcut durum dosyasına karar alanı.
- `meteor-ops-journal.py`: yalnız değişen kararların mevcut günlüğe yazımı.
- `monitor_base.py`, `index.html`: mevcut API ve AutoPilot görünümü.
- Önceki RC221 yönetim/camera kodları aynen bu sürüme kopyalandı; kamera timerı RC221'de kalır.
- Drop-in'ler: `meteormesh-monitor.service.d/97-rc222-policy-view.conf`, `meteormesh-status.service.d/~rc222-policy.conf`.

Rollback: yalnız bu iki drop-in'i kaldır, `systemctl --user daemon-reload`, ardından yalnız monitör servisini yeniden başlat. Status doğal sonraki turda önceki RC220 OpsJournal'ı kullanır; mevcut küçük durum alanını eski kod yok sayar. Eski alert durumunun salt kopyası sürüm dizininde0600 olarak saklanır. Aktif olayları kaybetmemek için eski snapshot körlemesine geri yazılmaz.
