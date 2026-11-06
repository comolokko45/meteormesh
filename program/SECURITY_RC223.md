# RC223 — yedi P1 bulgusunun doğrulanması ve düzeltmesi

6 Kasım 2026, canlı G62 kodu ve yayımlanmış kaynaklar karşılaştırıldı. Yedi bulgunun da kod karşılığı doğrulandı. Telefon tarafındaki silme açığı ayrıca depolama yönetim anahtarı ve Android All Files izni gerektiriyordu; kimlik doğrulamasız uzaktan silme iddiası değildir.

| Bulgu | Doğrulanan neden ve düzeltme | Kabul |
|---|---|---|
| Boş SHA ile silme | `sha.isEmpty() || ...` hash kontrolünü atlatıyordu. Tüm batch için64 küçük-hex SHA önkontrolü zorunlu; bir öğe eksikse hiçbir silmeye başlanmaz. Hash sonrası boyut/mtime tekrar bakılır. Hash kontrolünü atlayabilen eski `delete-tree` reddedilir; doğrulanmış dosya batch'i kullanılmalıdır. | Android JVM4 test ve APK derleme PASS; telefon kurulumu bekliyor |
| Temizlik hatasında exit0 | İç kanca, iki üst kanca ve backup hata yolu sonuç kodunu yutuyordu. Backup/retention/report hatası nonzero döner ve systemd'ye ulaşır; lock nedeniyle güvenli erteleme ayrı DEFERRED'dir. Hatalı JSON/ERROR metni başarı sayılmaz. | Hedefli katman testleri PASS; G62 dağıtımı etkin |
| Saat dışı analiz hatasının bekleme sayılması | Saat kontrolü FAILED kontrolünden önceydi. FAILED olay/iş ve servis/postprocess başarısızlığı, normal saat beklemesinden önce değerlendirilir. Servis hatası otomatik olarak güncel gece işi sayılmaz; kapsamın belirsiz olduğu belirtilir. | Gece23:00 senaryoları PASS |
| Sabah kabulünde timer kanıtı yok | Eski kod yalnız saat+aynı gece+analysis_complete ile doğal kabul veriyordu. Şimdi gerçek `systemctl show` LastTriggerUSec, predawn başlangıcı/sonucu ve invocation ID okunur. Beklenen05:30 ile zaman korelasyonu, doğru gece ve taze toplama zamanı zorunlu. Kanıt eksikse PENDING kalır. | Eksik/yanlış/tutarlı proof senaryoları PASS; gerçek sonraki sabah bekliyor |
| Bozuk telemetride çökme | None/list alanlara zincir `.get` çağrıları vardı. Assessment/devices/storage/purge/units/capture/queue/previous alanları tür denetimiyle normalize edilir. Bilinmeyen kuyruk başarılı/boş sayılmaz. | Bozuk alan matrisi PASS |
| Restore güvenliği assert | `-O` hash/yol/arşiv denetimlerini kaldırabiliyordu. Tüm çalışma zamanı assert'leri açık istisnalara çevrildi, symlink/yol sapması ayrıca reddedilir. | `python -O` saldırı yolu testi ve gerçek paket restore PASS |
| Başarısız boş DB yarım dosya | Son isimde boş dosya açılıp sonra şema uygulanıyordu. Artık aynı diskte geçici DB, şema+quick_check+fsync sonrası atomik ve üzerine yazmayan link yayımı var. Hata halinde geçici dosya temizlenir. | Bozuk şema/başarılı init/mevcut DB testleri PASS |

## Test ve dağıtım sınırı

Python6 unittest grubu (birden çok alt senaryo), watcher regresyonu, Android4 JVM testi ve `assembleDebug` geçti. Uzak silme testleri gerçek kullanıcı dosyası silmedi. G62 status ve analyze post-hook'ları sürümlü RC223 yollarına alındı; hiçbir kayıt/analiz servisi restart edilmedi.23:41 doğal status turu success. Güncellenmiş kurtarma paketi `python -O` altında geçici köke1.689 dosya ile açıldı; gerçek OS'ye yeniden kurulum yapılmadı.

Yeni MeteorNode: `0.13.7-pin0000-security1`, versionCode20. APK: `MeteorMesh-RC223-P1-Fixes-2026-11-06/dist/MeteorNode-0.13.7-security1.apk`. SHA-256 `3589f002962cad4572ebf82e7da6c9f3425802eaa2adc2de60cd8bb6e01b382a`.

Telefonlar gece kaydında olduğu için APK kurulmadı. Eski0.13.6 üzerindeki depolama API açığı cihazda yeni sürüm kurulana kadar sürer. Yeni sürüm kurulduktan sonra üç telefonda boş/eksik SHA'nın reddi ve normal verified purge kabulü ayrıca yapılmalıdır. Watcher yeni sürümü aynı mevcut listener güvenlik koşullarıyla tanır.

## GitHub ve kurtarma

Okunabilir program ağacına yeni RC223 kaynakları ve override'ları, Android0.13.7 kaynak/APK'sı, düzeltilmiş restore/boş DB araçları eklenir. Tarihsel eski sürümler rollback/forensic kaydıdır; içlerinde eski davranış bulunabilir. Güncel restore, SHA ile doğrulanan `security-update-rc223.tar.gz` katmanını uygulayarak yeni override'ları kurar. Eski frozen temel arşivi tek başına güncel güvenlik sürümü sayılmamalıdır.

Rollback: `~/.config/systemd/user/meteormesh-status.service.d/~rc223-p1.conf` ve `~/.config/systemd/user/meteormesh-analyze.service.d/~rc223-p1.conf` kaldırılır, `systemctl --user daemon-reload` yapılır. Sonraki doğal turlar önceki kancaları kullanır; kamera restart edilmez. Bu rollback giderilen hataları geri getirir; yalnız gerektiğinde uygulanmalı. Telefon APK'sı hâlâ eski sürüm olduğundan telefon rollback'i yapılmadı.
