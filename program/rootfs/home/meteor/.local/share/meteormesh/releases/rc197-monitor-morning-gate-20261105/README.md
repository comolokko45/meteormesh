# RC197 — Sabah raporu gece eşleşmesi

RC196, 19:02'deki gece aktarım raporunu gün içindeki 09:00 timer çalışmasından sonraki bir sync olduğu için sabah raporu olarak gösterebiliyordu. RC197 gece kimliğinden sonraki günün 09:00 zamanını hesaplar; bu zamandan önce o gecenin sabah işlemini bekliyor gösterir. Saat geldikten sonra aynı geceye ait sync raporu bulunsa bile raporun 09:00 servisine ait olduğu ayrıca kanıtlanana kadar bunu açıkça belirtir.

G62 release: `~/.local/share/meteormesh/releases/rc197-monitor-morning-gate-20261105`. Etkin drop-in: `~/.config/systemd/user/meteormesh-monitor.service.d/70-rc197-morning-gate.conf`. SHA-256: `76d6fb8d778145219f50da4833c3e90ad44c84fb2d88f1626ab33b7e889c5fd5`.

Hedefli kontrol: 5 Kasım 19:20'de 19:02 sync raporu varken 6 Kasım 09:00 bekleniyor; 6 Kasım 09:15 senaryosunda sync raporunun 09:00 servisine aidiyeti doğrulanmamış olarak gösteriliyor. Canlı HTTPS API ilk senaryoyu doğruladı. Gerçek 6 Kasım 09:00 çalışması henüz kanıtlanmadı.

Geri alma: yalnız `70-rc197-morning-gate.conf` dosyasını kaldırıp `systemctl --user daemon-reload` ve `systemctl --user restart meteormesh-monitor.service` çalıştır. RC196 yapılandırması ve kaynağı korunur.
