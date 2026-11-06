# RC199 — Analiz hatasında negatif silmeyi durdurma

RC198 post-hook yalnız `exec-condition` sonucunda retention zincirini atlıyor, diğer başarısız systemd sonuçlarını normal hook'a devrediyordu. Marker ileride true olursa, o tur başarısız olsa bile geçmiş başarılı işlerde `--apply` çalışabilirdi. RC199 yalnız `SERVICE_RESULT=success` olduğunda mevcut hook'a geçer; hata, zaman aşımı, sinyal, condition skip ve bilinmeyen sonuçlarda mevcut SQLite yedeğini çalıştırır, retention çağırmaz.

G62 release `~/.local/share/meteormesh/releases/rc199-retention-failclosed-20261105`. Analyzer drop-in `*zz-rc199.conf` yalnız ExecStopPost'u değiştirir. Kod SHA-256 `bc64ae1a16c09ad6b839badb179ef5c5093571db69cd6ec2e3cf432487c827ea`. İzole mock testleri beş hata/skip/unknown sonucu ve success dalını Windows ve G62 üzerinde doğruladı. Canlı systemd effective ExecStopPost doğrulandı; doğal başarısız analiz koşulu henüz oluşturulmadı. Marker false; kayıt/analiz servisi restart edilmedi.

Geri alma: yalnız `*zz-rc199.conf` drop-in'ini kaldırıp `systemctl --user daemon-reload` çalıştır; RC198 geri gelir. HDD silme politikasını kapalı tut.
