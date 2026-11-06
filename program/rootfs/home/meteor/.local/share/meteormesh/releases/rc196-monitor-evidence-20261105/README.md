# RC196 — Monitör kabul açıklamaları

RC192 kaynak temelinde beş sabit kabul açıklaması düzeltildi. Eski metinler güncel durumdan bağımsız olarak gece kaydının aktif ve son aktarımın 3/3 başarılı olduğunu iddia ediyor, F7 Ultra ilk erişimini ise bekliyor gösteriyordu. Yeni açıklamalar mevcut ilgili bölümlere yönlendirir ve eksik kabulü açık bırakır. Puanlar, monitörün kaynak verisi ve kayıt/analiz servisleri değiştirilmedi.

G62 release: `/home/meteor/.local/share/meteormesh/releases/rc196-monitor-evidence-20261105`. Etkin drop-in: `~/.config/systemd/user/meteormesh-monitor.service.d/60-rc196-evidence.conf`. Kaynak SHA-256: `49dbc8767ea29556726a174487e46ed74c6502eb30b740ea4d92cfcaf95d9d3c`. Python derleme kontrolü ve gerçek localhost/HTTPS API içerik kontrolü başarılı.

Geri alma: yalnız `60-rc196-evidence.conf` dosyasını kaldır, `systemctl --user daemon-reload` ve `systemctl --user restart meteormesh-monitor.service` çalıştır. RC192 drop-in ve release korunur. Bu işlem yalnız monitör erişiminde kısa kesinti oluşturur.
