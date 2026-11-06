# MeteorMesh — İlk beş final maddesi, RC217

6 Kasım 2026, 14:22 TRT canlı incelemesi. Sonraki canlı kabul ekleri aşağıdadır.

## 1. Negatif video temizleme kapasitesi — PARTIAL

Kök neden: Her analiz turu dokuz işi tamamlayabilirken eski temizlik kancası en fazla beş negatif dosya siliyordu. Boş kaynak kuyruğunda devam yolu RC216 ile eklenmişti; beş dosyalık kapasite günlük 360 segment üretiminin gerisinde kalabiliyordu.

RC217 üretime alındı. Aynı kanca en fazla iki ayrı beşli parti çalıştırır. İkinci parti yalnız ilk parti beş başarılı silme ile limite ulaştığını açık JSON sonucu olarak bildirirse başlar. Her parti öncesinde yeni SQLite yedeğinde tam `MM153_DB_BACKUP: PASS` satırı aranır. Politika tekrar okunur. Eksik sonuç, hata, kilit nedeniyle atlama, timeout veya beşten az silme ikinci partiyi durdurur. Son rapor bir kez çağrılır.

RC210'un dosya başına SHA, ffprobe, UUID, kamera durumu, zaman penceresi, aday/komşu kamera koruması, tombstone ve exclusive lock kontrolleri değiştirilmedi. Sürekli negatif silme için kullanıcının mevcut onayı kullanıldı. Manuel toplu silme yapılmadı.

Tur başına üst sınır artık 10 dosyadır. Saatte dört tur için teorik sınır 40 dosya; analiz üst sınırı 36 iş/saattir. Gerçek hız uygun dosya sayısına ve koruma kontrollerine bağlıdır. Bu hesap günlük tüm videoların silineceği garantisi değildir. Doğal boş-kuyruk devam kabulü hâlâ bekliyor.

12 hedefli senaryo geçti: sınırlı parti sayısı, eksik/atlama çıktısı, hata/timeout, dry-run ve özellikle ikinci yedek başarısızken ikinci silmenin engellenmesi. Eski test paketleri tekrar çalıştırılmadı. Monitör metni parti başına 5/tur başına 10 olarak düzeltildi; kanıtsız PASS verilmedi.

## 2. Analiz kuyruğu ve sonuç raporu — PARTIAL

20261105-night için 14:22 kesitinde 281 SUCCEEDED, 78 WAITING, 1 RUNNING, 0 FAILED vardı. Aynı doğal analiz turu 14:22:27'de başarıyla bitti; kalan kaynaklı iş sayısı 78 oldu. Tarihsel 99 kaynak-eksik ve 119 FAILED iş güncel geceden ayrı tutuluyor. Kuyruk ilerliyor; yeni analiz işçisi açılmadı, çalışan analiz durdurulmadı.

360 segmentin üç cihaz için dağılımı 120/120/120; telefon purge fişi 360, çözümlenmemiş girişim 0. Son rapor ara tur raporudur. Güncel gecenin tüm işlerinin bitişi ve son rapor eşleşmesi henüz tamamlanmış kabul edilmez. Mevcut 36 iş/saat temposunda 78 iş yaklaşık 2 saat 10 dakika ek analiz kapasitesi ister; bu tahmindir.

## 3. 05:30 başlangıcı, 09:00 kapalı — PARTIAL

6 Kasım doğal 05:30 başlangıç kanıtı mevcut. Predawn timer bir sonraki çalışmayı 7 Kasım 05:30 TRT gösteriyor. 09:00 `meteormesh-morning.timer` disabled/inactive doğrulandı. Bu servisin kendisi korunuyor; 05:30 zinciri onu kullanıyor. Analiz turları ve diğer gerekli timerlar etkin.

Son düzeltmelerden sonraki yeni doğal 05:30 kabulü henüz zaman olarak gerçekleşmedi. Saati değiştirerek veya manuel tetiklemeyi doğal çalışmaymış gibi göstererek PASS verilmedi.

## 4. Tam gece segment sürekliliği — PARTIAL; sidecar sınır ölçümü PASS

5 Kasım 19:00–6 Kasım 05:00 kapsamındaki 360 segment sidecar'ı SQLite kimliği ve SHA alanlarıyla karşılaştırıldı. Eksik veya uyuşmayan sidecar yok. Her cihazda 119, toplam 357 komşu sınır ölçüldü.

| Cihaz | Segment | PTS aralık min–max | 100 ms üzeri / sıfır veya negatif |
|---|---:|---:|---:|
| Ruby | 120 | 33,351–33,779 ms | 0 / 0 |
| Surya | 120 | 33,329–33,330 ms | 0 / 0 |
| Viva | 120 | 33,333–33,792 ms | 0 / 0 |

Sınırlar yaklaşık bir kare süresindedir. Bu ölçüm sidecar PTS/sensör verisidir; bütün MP4 paketlerinin/karelerinin tekrar taranması veya mutlak UTC doğruluğu değildir. `segment_boundary_verified=false` ve `UNVERIFIED_FIRST_FRAME_UTC` işaretleri değiştirilmedi. Mutlak UTC/ilk kare ve ham video düzeyindeki tam kabul açık kalır.

## 5. Kesinti sonrası devam ve reboot — PARTIAL

Gerçek RC191 `pull_one` kodu izole yerel HTTP sunucusuyla sınandı. 2.660.000 baytlık sentetik aktarım 1.100.000 baytta kesildi. `.part` korundu; ikinci istekte tam `Range: bytes=1100000-` kullanıldı; tamamlanan dosyanın SHA'sı doğrulandı. Üçüncü çağrı yeni indirme yapmadı. Sonuç PASS; bu test telefon/Wi-Fi kesintisi veya gerçek G62 reboot kabulü değildir. Üretim ağ bağlantısı kesilmedi, kullanıcı dosyaları kullanılmadı.

G62 kullanıcı linger ayarı açık; Tailscale enabled/active. Capture, pull, predawn, analiz turları, status timerları ve monitör enabled. Analizde sync çakışma kontrolü, exclusive lock ve ağ hazır kapısı etkin. HDD bağlı, beklenen UUID eşleşiyor; SQLite monitör sağlık sonucu OK.

Canlı analiz sürerken reboot yapılmadı. Parolasız sudo erişimi bulunmadığından güncel SMART/reboot ayrıca uygulanamadı. Gerçek yeniden başlatma sonrasında SSH müdahalesiz devam kanıtı hâlâ açık.

## Değişiklik ve rollback

G62 sürümü: `~/.local/share/meteormesh/releases/rc217-cleanup-capacity-20261106/`

- `after_analysis_with_backup.py`: iki sınırlı parti, her parti öncesi yedek.
- `cleanup_gate.py`: mevcut RC216 doğrulamalarını yeni kancaya yönlendirir.
- `post-analysis-condition-gate.py`: başarılı sonucu yeni kancaya yönlendirir; hata yolunda yalnız yedek davranışı korunur.
- `monitor/index.html`: yeni sınırın açıklaması; monitör sunucu kodu RC215'ten aynen kopyalandı.
- Analiz drop-in: `~/.config/systemd/user/meteormesh-analyze.service.d/~rc217-cleanup-capacity.conf`
- Monitör drop-in: `~/.config/systemd/user/meteormesh-monitor.service.d/94-rc217-cleanup-label.conf`

Rollback: yalnız bu iki RC217 drop-in dosyasını kaldır, `systemctl --user daemon-reload`, ardından yalnız `meteormesh-monitor.service` yeniden başlat. Analyzer veya capture restart gerekmez. RC216/RC215 dosyaları korunur. Yazılım rollback'i daha önce onaylı politikayla silinmiş negatif videoları geri getirmez.

Yerel kanıtlar: `MeteorMesh-RC217-First-Five-2026-11-06/rc217-state.json`, `rc217-boundaries.json`, `rc217-resume.json`, hedefli test kaynakları. G62'de sürüm manifesti önceki hook ve SHA'ları saklar.

## Kalan gerçek kabuller

Boş-kuyrukta doğal temizlik devamı; güncel 78 kaynaklı işin kapanışı ve sonuç raporu; 7 Kasım yeni 05:30 doğal akışı; tam kare/UTC doğrulaması; güvenli bakım zamanında gerçek reboot ve telefon aktarımı kesinti kabulü. Bu bekleyen kanıtlar nedeniyle ilk beş madde topluca FINAL/PASS ilan edilmedi.

## 14:24 TRT doğal RC217 kabulü

Çalışmakta olan analiz turu yeniden başlatılmadan normal biçimde tamamlandı. systemd yeni post-hook'u kullandı. `natural-hook.log` içinde sırasıyla iki ayrı `MM153_DB_BACKUP: PASS`, `batch=1 status=OK`, `batch=2 status=OK`, `REPORT=OK`, `DELEGATED exit=0` ve servis bitişi mevcut. Her parti beş uygun negatif MP4 sildi; toplam 10. Eksik komşu kamera kapsamı olan 10 dosya yine korundu.

Bu doğal iki-parti kabulü PASS. Boş kaynak kuyruğunda çalışma yolu henüz doğal olarak tetiklenmediği için birinci maddenin bütünü PARTIAL kalır.

Son canlı durum: güncel gece 282 SUCCEEDED / 78 WAITING / 0 RUNNING / 0 FAILED. Global `NO_CANDIDATE_DELETED` sayısı 106'dan 116'ya yükseldi. HDD boş alanı 277,95 GiB. API/healthz erişilebilir. Kanıtlar: `natural-hook.log`, `live-after.json`, `manifest.json`.
