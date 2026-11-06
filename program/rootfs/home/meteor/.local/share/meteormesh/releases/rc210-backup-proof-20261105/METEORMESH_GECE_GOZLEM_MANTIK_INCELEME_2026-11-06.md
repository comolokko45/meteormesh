# MeteorMesh gece gözlemi ve mantık incelemesi

Durum: sürüyor. G62 ve bu Windows oturumu 6 Kasım 2026 Avrupa/İstanbul saat diliminde. Salt-okunur gözlem 00:05 TRT'de başladı; `MeteorMesh-Overnight-Watch-2026-11-06.ps1` PID 4372 her dakika G62 `127.0.0.1:8765/api/state` örneğini `MeteorMesh-Overnight-Watch-2026-11-06.jsonl` dosyasına kaydediyor. İzleyici 09:15'e kadar çalışacak. Kayıt/analiz tetikleme, yeniden başlatma, servis değişikliği veya silme yapmıyor.

## İlk canlı örnek — 00:08 TRT

- G62 API `CURRENT`; son telefon durumu yaklaşık 4 dakika eski, 10 dakikalık tazelik sınırının içinde.
- Ruby, Surya, Viva HTTP ve Control-013 `ONLINE`, kamera `recording`; ADB üçünde de `DISCONNECTED`. ADB durumunun Node/kamera arızası olmadığı doğrulanıyor.
- `20261105-night`: 60 segment/telefon, toplam 180 verified, 0 analiz başarısızlığı. 00:02 TRT civarı saatlik pull `INGEST_QUEUED`, cihaz başına 1 yeni segment; her cihazda toplam 60 telefon purge fişi, unresolved=0.
- Surya boş alanı 33.43 GiB, koruma rezervi 32 GiB; `STORAGE_LOW:surya` tek açık ve birleştirilmiş OpsJournal olayı. Gerçek rezerv ihlali yok.
- HDD doğru UUID ile bağlı, yaklaşık 294 GiB boş. SQLite `OK`. Sistem monitörü RAM toplamını 7739 MiB (yaklaşık 7.6 GiB) gösteriyor.
- Analiz penceresi dışında `ANALYSIS_WINDOW: SKIP` beklenen; SQLite yedeği `PASS`, RC199 `SERVICE_RESULT=exec-condition` için retention'ı doğru biçimde atlıyor. Gecenin HDD temizliği kapalı.
- 19:00–00:05 arası izlenen birimlerin journal'ında warning/alert önceliğinde kayıt yok. Capture tikleri `PASS`; gerçek telefon/aktarımı `PARTIAL_REVIEW_REQUIRED` gösteren bir gece olayı görülmedi.

## Kaynak ve sıra incelemesi

`meteormesh-predawn.timer` 05:30'da sync servisini başlatıyor; yalnız sync başarılı olursa analyzer'a `OnSuccess` veriyor. Sync `--phase sync` ile çalışıyor, aktif kapsamı yeniliyor. Analyzer'ın saat kapısı 05:30–18:49, analiz turu zamanlayıcısı 15 dakikada bir; ayrıca analiz kaynağı yoksa/no-actionable-source ise tur atlanıyor. 09:00 servisi de sync-only. Analyzer'ın önkoşulu hourly pull/midnight/morning servislerinin ve ortak transfer kilidinin boş olmasını arıyor; böylece 09:00 transferini aynı anda analizle çakıştırmaması tasarlanmış. Sabah gerçek sıralaması ve kaynak kapsamı henüz bekleniyor.

Gece kapsamı şu anda dosyada `20261104-night`, canlı kayıt `20261105-night`. Bu 05:30 scope yenilemesinden önce beklenen durum. Doğal analiz başlamadan önce güncel kapsamın `20261105-night` olduğunu kontrol edeceğim. 09:00 raporu analiz tamamlandı demek olmayabilir: mevcut 9 iş/15 dk hızı nominal 36 iş/saat; 360 gece videosu ve yaklaşık 24 kaynaklı carryover ≈384 iş için kabaca 10.7 saat gerekir. Bu, 13 saat 20 dakikalık pencereye sığabilir; 05:30–09:00 arasında ise yaklaşık 126 işlik ilerleme beklenir. Gerçek kuyruk hızı ve tamamlanma G62 loglarıyla ölçülecek.

## Mantıksal güvenlik kapıları

HDD negatif video silme kullanıcı tarafından onaylanmış olsa da marker kapalı. RC208 uygulaması gece saatlerinde veya kamera `recording` iken silmeyi reddediyor; silme öncesi kameraları tekrar sorguluyor. Gerçek segment timestamp'leri hâlâ `UNVERIFIED_FIRST_FRAME_UTC` ve sınır doğrulanmamış; üç kamera arası kesin örtüşme kanıtı yok. G62 sağlık sorgusu ile telefondan manuel kayıt başlatma arasında teorik yarış da mevcut. Bu nedenle gece boyunca silme açılmayacak; uygun gündüz penceresinde de yalnız koşullar sağlanırsa onaylanmış tek dosya pilotu değerlendirilebilir.

SMART canlı okuması meteor kullanıcısında `/dev/sdb` izin hatası veriyor, `sudo` parolası gerekiyor. Önceki 446 yeniden eşlenen sektör kaydı var. Canlı SMART sonucu yok. İki eski `.part` dosyasının hedef MP4/SQLite kaynakları yok; gerçek kesinti sonrası Range resume kabulü değiller.

Yerel OpsJournal ve Bilgi Monitörü uyarı gösterebiliyor, fakat harici telefon push teslimi yapılandırılmamış. Doğal gece başlangıcı, tamamlanmış gece sayısı veya analizde aday/negatif sonuç kanıtı olmadan meteor kabulü verilmeyecek.

## Yeni kritik bulgu: HDD büyüme hızı

00:15 TRT SQLite salt-okunur sorgusunda 19:00'dan sonraki ilk 62 doğrulanmış segmentin boyutları: Ruby 14.89 GiB (ortalama 245.95 MiB/segment), Surya 14.94 GiB (246.68 MiB), Viva 5.74 GiB (94.73 MiB); toplam 35.57 GiB / yaklaşık 5.25 saat. 10 saatlik gecede mevcut hız korunursa yaklaşık 68 GiB beklenir. G62 HDD'de 293 GiB boş; veri işlemlerinin 100 GB/GiB civarı min-free kapısı göz önüne alındığında temizleme olmazsa sadece yaklaşık 2.8 tam gecelik ek kapasite kalır. Bu bir doğrusal tahmindir, fakat kameraların bitrate'i değişse bile risk büyüktür. Telefonlardaki karşılık dosyalar HDD doğrulamasından sonra purge edilmiş; ikinci MP4 yedeği yok.

Bu yüzden negatif temizliğin kapalı kalması uzun süreli depolama açısından da sürdürülebilir değil. Güvenli çözüm, kamera segmentlerinin gerçek başlangıç/bitiş ve mutlak zaman belirsizliğini doğrulayacak kaynak kanıtını üretip onaylı temizlik pilotunu gündüz çalıştırmaktır. Eşik veya komşu-kamera korumalarını gevşetmek güvenli bir çözüm sayılmaz. Bu gece silme yapılmadı.

00:17 TRT'de API bir geçici `NO_JOB_SEGMENTS=1` bildirdi; sonraki 00:18 aktarımı üç yeni segmenti kuyruğa ekledi ve değer 0 oldu. Bu periyodik sync aralığında beklenen kısa gözlem farkı; takip örneğinde kayıp veya kalıcı kuyruk uyumsuzluğu değil.

## Sabah kontrol listesi — henüz sonuç değil

05:00 sonrası: her cihazın kamera durumu durdu mu, son segment tamamlandı mı, READY/manifest ilerledi mi? 05:30: scope dosyası doğru gece mi, sync 3/3 mü, analyzer yalnız sync tamamlandıktan sonra mı çalıştı, RC210 backup fişi ve retention kapısı ne yaptı? 09:00: sabah sync'in `night_id`, bütün cihazların son segmentleri, rapor kapsamı, analiz kuyruğu ve durum mesajı doğru mu? 09:00 sonrasında kuyruk 18:50'ye kadar düşüyor mu? Disk rezervi ve telefon bağlantıları korunuyor mu?
