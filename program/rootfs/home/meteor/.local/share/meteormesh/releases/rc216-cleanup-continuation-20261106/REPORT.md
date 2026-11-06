# MeteorMesh RC216 — 6 Kasım 2026

Son canlı kesit13:38 TRT: güncel gece255 SUCCEEDED /105 WAITING /0 RUNNING /0 FAILED. Kuyruk bitmedi. Genel açık uyarı Surya rezerv başlığı;119 eski FAILED ayrıca korunuyor.

## Yapılan düzeltme

Mevcut analyzer ExecCondition kuyruğu boş veya yalnız kaynağı eksik işleri görünce analiz turunu atlıyordu. RC210 post-hook bütün exec-condition sonuçlarında temizliği de atladığı için uygun negatif dosyalar birikebiliyordu. RC216 yalnız exec-condition dalında durumu yeniden salt-okunur değerlendirir: gündüz05:30–18:45 penceresi, meşgul sync olmaması, NightScope/config doğrulayan mevcut kuyruk guard, mevcut kaynak guard çıktısında0 çalıştırılabilir kaynak, global RUNNING=0. Hepsi doğrulanırsa mevcut RC210 başarılı-kanca yolunu çağırır. Başarısız/timeout/signal/bilinmeyen sonuçlar önceki davranışta kalır. Kayıt sessizliği, NTP, UUID, backup, lock, SHA, aday ve komşu kapsam, tombstone kontrolleri yine mevcut retention motorundadır. Çağrı sınırı5, yeni timer/agent yok.

## Doğrulama

17 izole politika/dispatch/guard senaryosu PASS. Canlı salt-okunur önkontrol105 kaynaklı iş varken cleanup_allowed=False döndürdü. Etkin systemd ExecStopPost RC216 dosyasını gösteriyor. Analyzer restart edilmedi; elle silme tetiklenmedi. Gerçek boş-kuyruk dalı henüz doğal olarak çalışmadı: operasyon kabulü PARTIAL.

## Kalan sınırlar

Bu değişiklik temizliğin kuyruk boşalınca ilerlemesini sağlar; günlük kapasite açığını tamamen kapatmaz. 15 dakikada5 dosya ve sınırlı gündüz penceresi yaklaşık265 dosya/gün üst sınırıdır;360 negatif video/gün üretiminde yine birikme olabilir. Sınırı artırmadan tüm negatiflerin her gün temizleneceği söylenemez. Gerçek uygun dosya sayısı ve korunan aday/komşu kapsam ayrıca ölçülmeli. Önceki RC215 raporundaki kapasite sorununun durumu bu nedenle PARTIAL kalır.

SMART yönetici erişimi, Mobile1.0.1 gerçek cihaz kabulü, frame/UTC bilimsel kanıt, kontrollü reboot/Range-resume ve çok geceli gözlem açık. Tüm kaynak kodun hatasız olduğu veya finalin tamamlandığı iddia edilmez. Geniş sistem incelemesi RC215 raporunda; bu tur doğrulanan temizlik kontrol akışına odaklandı.

## Dosyalar ve rollback

G62 release: ~/.local/share/meteormesh/releases/rc216-cleanup-continuation-20261106
Kaynak: rc216_cleanup_gate.py. Manifest önceki ve yeni hook'u ve SHA256'yı kaydeder.
Geri alma: yalnız ~/.config/systemd/user/meteormesh-analyze.service.d/~rc216-cleanup-continuation.conf kaldır; systemctl --user daemon-reload. Sonraki tur RC210'a döner. Çalışan analyzer'ı yeniden başlatma.
