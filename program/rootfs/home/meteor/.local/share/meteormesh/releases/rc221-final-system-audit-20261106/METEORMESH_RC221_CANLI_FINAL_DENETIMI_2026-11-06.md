# MeteorMesh RC221 — canlı sistem ve kod denetimi

6 Kasım 2026, 22:06 TRT. İnceleme canlı G62 üzerinden yapıldı; çalışan gece kaydı devam etti.

## Sonuç

Üç somut hata düzeltildi ve üretime uygulandı. Önceki 20261105-night gecesinin 360 işi SUCCEEDED. Yeni gece için 108 iş WAITING, 0 FAILED; 05:30 kapsam değişimi bekleniyor. Sistem hata vermediği hâlde bütün kabul kapıları kapanmış değildir. Kod tamamlanma yüzdesi bu denetimden hesaplanamaz.

## Kapsam

Etkin systemd başlangıç yolları tespit edildi. MeteorHub RC191/RC211, temizlik RC217/RC210 bağlantıları, yönetim RC218, Agent RC220, Control-013, mevcut yerel yardımcılar ve MetDetPy içindeki 239 Python dosyası, toplam 55.072 satır AST ayrıştırmasıyla tarandı; sözdizimi hatası yok. Dosya yolu/SHA manifesti `rc221-sources.json` içinde. Bu otomatik kaynak taraması, 55.072 satırın tamamının mantıksal doğruluğunun tek tek kanıtlandığı anlamına gelmez. Yönetim, kayıt, dosya güvenliği ve zamanlama yolları ayrıca incelendi. Android 0.13.6'nın 13 Kotlin dosyasında kayıt/segment, depolama ve ağ yollarına yönelik tarama yapıldı; gece sırasında APK değiştirilmedi.

## Düzeltilen hatalar

### 1. Uzaktan komutun yanlış servis çalışmasına bağlanması

Önceki yönetim kodu, talep zamanından sonra biten herhangi bir aynı servis çalışmasını komutun başarısı sayabiliyordu. Talep kabul edilmez veya araya doğal timer girerse daha sonraki otomatik çalışmanın sonucu yanlış eşleşebilirdi.

RC221 her isteği systemd `InvocationID` ile bağlar. Komut gönderildikten sonra yeni çalışma kimliği doğrulanamazsa `DISPATCH_UNCERTAIN`; sonraki gözlemde farklı kimlik görülürse `SUPERSEDED_REVIEW_REQUIRED` döner. Aynı kimliğin başarıyla bitmesi `SERVICE_COMPLETED` olur. Bu sonuç dosya/analiz bütünlüğünün bağımsız kabulü değildir.

Hedefli testte sonraki timer kimliği başarı sayılmadı, doğru kimlik başarı sayıldı. Gerçek Tailscale HTTPS üzerinden durum isteği gönderildi, tekrarlı aynı istek yeni servis başlatmadı ve fişteki kimlik servis kimliğiyle eşleşti. TLS ve yetkisiz erişim reddi geçti. Kamera/silme komutu test amacıyla gönderilmedi.

### 2. Düşük alan korumasının kayıt ilerleme hatasına takılması

Kayıt kontrolü eski MP4 ilerlemesini denetledikten sonra 32 GiB sınırına bakıyordu. İki sorun aynı anda olursa kontrol hata verip depolama rezervini korumak için yapılması gereken sahip olunan kaydı durdurmaya ulaşamıyordu.

Sahip olunan kayıt için düşük alan durdurması, ilerleme kontrolünün önüne alındı. Normal alanlı durumda eski ilerleme kontrolü korunur. Kullanıcının başlattığı sahipsiz kayıt için otomatik durdurma yetkisi genişletilmedi. Hedefli testte düşük alan + eski kayıt durumunda tek stop ve ownership bırakılması doğrulandı; yeterli alanda stale kayıt yine hata verdi.

22:05 TRT doğal capture turunda yeni sarmalayıcı üç cihazda `RECORDING_OWNED`, `CAPTURE_TICK: PASS` üretti. Gerçek telefonu doldurup durdurma denemesi yapılmadı.

### 3. Bilinmeyen temizlik limitinin sıfır gösterilmesi

Monitör veri kaynağında limit alanı bulunmadığında bunu 0 olarak gösteriyordu. Eksik/yanlış tipli limit artık `VERİ MEVCUT DEĞİL` olarak raporlanır. Boolean değer geçerli sayı sayılmaz. Kanıt eksikken yazılım kapısı PARTIAL kalır; fiş sayısından sınır tahmini yapılmaz. Gerçek RC217 sınırı parti başına5, tur başına2 partidir. Eski monitör kaynağının bu sınırı otomatik taşımaması ayrı telemetri eksiği olarak sürer.

## Canlı veri kontrolleri

| Alan | Kanıt | Durum |
|---|---|---|
| Telefonlar | Ruby/Surya/Viva kayıt; HTTP ve Control online | PASS, anlık |
| 20261105-night analiz | 360 SUCCEEDED | PASS, kuyruk sonucu |
| Yeni gece | 108 WAITING; 05:30 bekleniyor | PARTIAL, beklenen süreç |
| SQLite | `PRAGMA quick_check=ok` | PASS |
| Silme fişleri | 289 NO_CANDIDATE_DELETED; cihaz/iş/SHA uyuşmazlığı0 | PASS, ilişkisel kontrol |
| Silinen kaynak | Silindi kayıtlı289 kaynaktan HDD'de yeniden mevcut0 | PASS |
| G62 servisleri | Başarısız user unit0 | PASS, anlık |
| HDD | Doğru mount/UUID; yaklaşık291 GiB boş | PASS, anlık kimlik/alan |
| Surya | 33,31 GiB boş;32 GiB koruma eşiği | PARTIAL, dar marj |
| 09:00 timer | disabled;05:30 timer enabled | PASS, yapılandırma |
| Tailscale | Özel HTTPS Serve etkin; loopback8765 | PASS |
| Agent | OBSERVE; OpsJournal ilk/son görülme bilgisi güncel | PASS, gözlem |

İki eski `.part` görüldü: Viva29Ekim ve Ruby31Ekim. Bunlar yeni aktarım başarısızlığı olarak sınıflandırılmadı ve otomatik silinmedi. Kaynak olmadan yeniden tamamlanmış sayılmaz. Tüm mevcut MP4'ler yeniden hash edilmedi; bu işlem canlı gece aktarımıyla aynı HDD'ye gereksiz yük getirirdi. İnceleme ilişkisel DB/SHA alanı kontrolleri ve mevcut doğrulama fişlerine dayanır.

## Final için açık kalanlar

1. Yeni 7 Kasım05:30 doğal kapsam değişimi ve gece kuyruğunun tamamlanması.
2. Gerçek reboot sonrası SSH komutu olmadan otomasyonun dönmesi; aktif kayıt sırasında denenmedi. Linger ve timerlar etkin. Güncel SMART için parolasız yönetici erişimi yok; tarihsel446 reallocated sektör riski sürer.
3. Android timed-muxer tam kare ve mutlak UTC kabulü. Kaynak hâlâ `segment_boundary_verified=false`, `UNVERIFIED_FIRST_FRAME_UTC` diyor; kanıtsız değiştirilmedi. Önceki357 sidecar sınır ölçümü tek başına tüm kare/UTC kabulü değildir.
4. F7 Ultra'da Mobile1.1.0 gerçek kurulum, yönetim oturumu ve kullanıcı kamera eylemi kabulü.
5. OpsJournal dış kritik bildirim teslimi etkin değil; yerel kayda yazmak push teslimi değildir.
6. Çok geceli güvenilirlik ölçümü. Bir başarılı gece %99 günlük otomasyon güvenilirliği kanıtı değildir.
7. Temizlik sınırı telemetrisinin monitör veri sözleşmesine bağlanması; veri yokken PARTIAL doğru davranış.

Tarihsel119 FAILED ve99 kaynak-eksik iş yeni gecenin hatası sayılmıyor. Kaynağı eksik tarihsel meteor adaylarına bilimsel meteor doğrulaması verilmez. Tüm hataların çözüldüğü veya tüm sistemin FINAL olduğu iddia edilmez.

## Dağıtım ve rollback

G62 sürümü: `~/.local/share/meteormesh/releases/rc221-final-system-audit-20261106/`.

- Değişen kod: `manage_server.py`, `capture_base.py`, `monitor_base.py`.
- Destek dosyaları: mevcut `capture_control.py`, `index.html`, `manage.html` kopyalandı.
- Monitör override: `~/.config/systemd/user/meteormesh-monitor.service.d/96-rc221-invocation-proof.conf`.
- Capture override: `~/.config/systemd/user/meteormesh-capture.service.d/~rc221-storage-priority.conf`.

Rollback yalnız bu iki override'ı kaldırıp `systemctl --user daemon-reload`, ardından yalnız monitor servisini yeniden başlatmaktır. Kayıt servisi doğal sonraki tick'te önceki RC218'e döner. Analyzer/capture yeniden başlatılmadı. AgentRC220, temizlikRC217, MeteorNode0.13.6 ve Mobile1.1.0 korunur. APK yeniden derlenmesi gerekmez; yönetim sayfası G62'den yüklenir.

Kanıt klasörü: `MeteorMesh-RC221-Full-Audit-2026-11-06/`: kaynak manifesti, canlı önce/sonra durumları, salt okunur DB kontrolü, HTTPS fişi, hedefli test ve dağıtım kaydı.
