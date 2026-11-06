# RC200 — Yerel kamera ağı hazır kontrolü (STAGING)

Mevcut capture ExecStartPre, LAN erişimi mevcutken Tailscale veya varsayılan internet rotası yoksa capture tick'ini başlatmayabilir. RC200, yalnız capture için yapılandırılmış telefonlardan en az birinin ağına Tailscale/gateway üzerinden geçmeyen doğrudan IPv4 route arayan bir helper hazırlar. Telefonların gerçek erişilebilirliği ve biri bozukken diğerleriyle devam etme kararı mevcut capture loop'ta kalır. Diğer sync/analiz/güvenlik kapıları değiştirilmez.

Dört izole kontrol Windows ve G62'de PASS: doğrudan LAN route, Tailscale/gateway reddi, ilk route başarısızken diğerinin kabulü ve desteklenmeyen endpoint reddi. G62 gerçek config ve route ile `CAPTURE_LAN_READY` döndü. Bu yalnız route varlığını gösterir; gerçek Tailscale kesintisi veya cihaz erişim testi değildir.

Durum: yalnız `~/.local/share/meteormesh/staging/rc200-capture-lan-gate-20261105` altında; production capture unit değiştirilmedi. Aktif gece kaydının dışında gerçek Tailscale kesintisi/toparlanması ve mevcut bir-telefon-bağımsızlığı kapsamları değerlendirilip ardından geri alınabilir systemd drop-in ile uygulama yapılmalıdır. Tailscale yapılandırmasına dokunulmaz.
