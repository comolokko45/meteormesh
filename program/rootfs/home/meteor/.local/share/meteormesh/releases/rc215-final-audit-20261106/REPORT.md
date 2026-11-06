# MeteorMesh kapsamlı final incelemesi — 6 Kasım 2026

Kanıt zamanı: 12:43–12:50 TRT. RC215 dağıtım API'si: 2026-11-06T12:47:08+03:00.
Bu rapor yerel kanonik harita, etkin systemd komutları, G62 canlı monitor API'si, SQLite salt-okunur sorguları, son 27 analiz turu, RC210 temizlik kancaları, RC211 analiz/sync kaynakları, ağ kapıları ve RC214/215 monitör kaynaklarına dayanır. Her tarihsel sürümün her satırının denetlendiği iddia edilmez. Android 0.13.6'nın gerçek kare/UTC doğruluğu bu turda ölçülmedi.

## 1. Canlı son durum

- Ruby, Surya, Viva HTTP API ve Control-013 ONLINE; MeteorNode 0.13.6-pin0000-timed-muxer-beta1. Kamera gündüz idle, remote camera ready=true; beklenen gündüz davranışı. ADB üçünde de DISCONNECTED, Node arızası sayılmıyor.
- 20261105-night: Ruby120, Surya120, Viva120 doğrulanmış segment; toplam360. Yaklaşık 68.92 GiB video envanteri. Beklenen 120/cihaz ile sayı eşleşiyor.
- Telefon temizliği: her cihaz için120, toplam360 deleted fişi; çözümlenmemiş purge denemesi0, fiş ayrıştırma hatası0.
- RC215 dağıtım anında analiz223 SUCCEEDED /136 WAITING /1 RUNNING /0 FAILED. Daha önceki 12:44 kesitinde221 sonuç dosyası doğrulanmış, eksik/bozuk sonuç0, bu tamamlanmış alt kümede aday0. Kalan videolar için henüz sonuç yok; geceye topluca 'meteor yok' denemez.
- Tarihsel99 kaynak-eksik iş ve119 FAILED güncel geceye eklenmiyor. Örneklemede bütün WAITING işlerin attempts=0; yeniden deneme döngüsünün kanıtı yok. Eski119 hata MetDetPy exit=1 kategorisinde; kısaltılmış hata metni tek başına kök nedeni kanıtlamaz.
- G62 RAM7739 MiB, kullanılabilir6991 MiB; analyzer o kesitte yaklaşık1.31 GiB MemoryPeak. HDD270.21 GiB, SSD82.22 GiB boş; HDD doğru UUID ile ext4 mount. Son SQLite yedeği yaklaşık0.1 saatlik; başarısız user unit0.
- Tailscale Serve yalnız tailnet üzerinden HTTPS → 127.0.0.1:8765. İnternete açık Funnel yolu yok; mevcut erişim korunuyor.

## 2. İşleyen zincirin değerlendirmesi

| Alan | Durum | Gerçek kanıt / sınır |
|---|---|---|
| Üç cihaz gece segment sayısı | PASS | 120×3 verified; tam kare sürekliliği anlamına gelmez |
| Aktarım ve telefon purge fişleri | PASS | 360 verified/purge, unresolved0 |
| Güncel meteor analizi | PARTIAL | İşler ilerliyor, FAILED0;136+1 iş sürüyor |
| 05:30 başlangıcı | PASS, tarihli | 6 Kasım doğal tetik var; RC211 değişikliği daha sonra kurulduğundan7 Kasım davranışı bekliyor |
| 09:00 iptali | PASS | morning.timer inactive/disabled; morning.service05:30 için korunuyor |
| Negatif HDD silmesi | PASS, sınırlı | 76 global tombstone; mevcut turun/gecenin silmesiyle karıştırılmıyor |
| Silme kapasitesi | PARTIAL | En fazla5/başarılı analiz kancası; günlük negatif yük için yetersiz olabilir |
| AutoPilot | PARTIAL | Sınırlı listener/transfer/kuyruk kurtarma var; her hatayı çözen genel ajan değil |
| RC215 monitor | PASS, hedefli |6 yeni senaryo, health/API ve etkin drop-in doğrulandı |
| Mobile1.0.1 | PARTIAL | İmzalı APK/derleme/lint geçti; F7 gerçek kabulü açık |
| SMART | BLOCKED | sudo -n parola gerektiriyor;446 sektör bilgisi tarihsel |
| Frame/UTC bilimsel doğruluk | PARTIAL | boundary_verified=false ve UNVERIFIED_FIRST_FRAME_UTC korunuyor |
| Reboot/gerçek Range resume/çok gece | PARTIAL | Bu turda kesinti veya reboot uygulanmadı |
| Harici kritik bildirim | PARTIAL | OpsJournal yerel; push teslimi kurulmuş değil |

## 3. Bu tur tamamlanan düzeltmeler

### Yerel kamera kontrolünün Tailscale bağımlılığı
Etkin capture ExecStartPre, yerel telefon ağı çalışsa bile Tailscale/default route gerektiriyordu. Daha önce hazırlanmış ve4 izole senaryosu geçmiş RC200 helper staging'de kalmıştı. Kaynak yeniden incelendi; doğrudan telefon LAN rotası, en az bir cihazın rotası ve desteklenen endpoint sınırları korunuyor. RC215 capture drop-in yalnız ExecStartPre'yi bu helper'a yöneltti; timeout60s, servis bütçesi90s. Telefon erişim ve cihaz başına hata yönetimi mevcut capture loop'ta kaldı. Canlı helper CAPTURE_LAN_READY döndü. Gerçek VPN kesintisi yapılıp test edilmedi; remote erişim bilinçli kesilmedi.

### Gece sonu eksik kapsamanın kaybolması
current_segments yalnız observed<night_end iken son segment sonrası boşluğu hesaplıyordu. Gece bittikten sonra eksik son aralık kayboluyordu; hiç segmenti olmayan telefon için boşluk kaydı da oluşmuyordu. RC215 eksik aralığı min(gözlem zamanı,05:00) ile sınırlar ve boş cihazı NO_VERIFIED_COVERAGE gösterir. Kapsam G62'ye doğrulanmış segmentlere aittir; telefonda fiziksel çekimin kaybolduğunu tek başına ispatlamaz. Testlerde kapanmış gecede35.700s eksik kuyruk, boş cihazda36.000s eksik kapsam ve aktif segmentin toleransı doğrulandı. Gerçek son gece envanterinde büyük boşluk yok;6 dakikalık eşik altındaki kare/saniye boşlukları için ayrı inceleme gerekir.

### Etkin temizlik ile geçmiş raporun çelişmesi
Canlı marker true iken eski analiz raporunun retention_enabled=false alanı ekranı DISABLED_PENDING_EXPLICIT_APPROVAL yapıyordu. RC215 canlı politikayı ENABLED_EXECUTION_GATED diye gösterir ve etkin politika için tekrar onay bekleniyor demez. Tarihsel run alanları ayrıca korunur. Eski silme fişi varken politika sonradan kapatılmışsa operasyon kapısı artık PASS üretmez. Silme motoru veya silme limiti değiştirilmedi.

## 4. Veri koruma ve kapasite incelemesi

76 NO_CANDIDATE_DELETED satırının tamamı mevcut segment ve başarılı analiz işiyle eşleşti; cihaz/segment/MP4 SHA tutarsızlığı0, eksik sonuç SHA alanı0. Bu metadata tutarlılık kontrolüdür; silinmiş MP4 içeriğini yeniden hash'leme iddiası değildir. Mevcut otomasyon toplam15.678.128.027 baytlık global video temizliği raporluyor. Bu tur elle dosya silinmedi.

27 adet dokuz işlik doğal analiz turunun ortalaması742.71s, en yavaşı831.29s. 15 dakikada9 iş yaklaşık36 iş/saat;360 yeni segment için10 saat nominal tur zamanı. Şimdiki136+1 iş yaklaşık3.8 saat daha gerektirir;16:35–17:00 civarı yalnız tahmindir, hata/lock gecikmesi veya yeni iş gelmesi bunu değiştirir. 18:50 guard ve19:00 kayıt önceliği korunuyor.

Önemli kalan kapasite kusuru: 9 analiz/tur karşısında5 silme/tur var.360 negatif video40 analiz turunda bitse bile kancalar en fazla200 dosya silebilir; koruma engelleri bunu azaltabilir. Ayrıca analyzer kaynak kalmayınca ExecCondition atlanır ve RC210 kapısı retention çalıştırmaz. Dolayısıyla negatif silme artığı sonraki başarılı analizleri bekleyebilir. Bu yalnız teorik20 dosya/saat sınırından daha sıkı bir günlük sınırdır. Limit yükseltme veya 'kaynak yok' sonrası güvenli temizlik devamı için mevcut kancada ayrı hedefli geliştirme gerekir; bu tur toplu silme kapsamı genişletilmedi.

Depolama projeksiyonu: 68.92 GiB/gece üretim ve270.21 GiB boş alanla100 GiB güvenlik rezervine kalan alan, hiç temizlik olmazsa yaklaşık2.47 geceye yeter. Gerçek negatif/aday oranı ve fiili temizleme bunu değiştirir. Surya33.68 GiB boş, koruma32 GiB;1.68 GiB başlık erken uyarısı geçerlidir. Rezerv azaltılmadı.

## 5. Finale kalan somut öncelikler

1. Analiz kuyruğu doğal bitince aynı gece raporunun terminal durumunu doğrula; şimdiden tamamlandı deme.
2. Negatif temizlik birikimini çöz: mevcut lock, backup, SHA, aday/komşu koruma ve tombstone kapılarını koruyarak kanca kapasitesini veya yalnız güvenli kaynak-yok evresini tasarla. Gerçek dosya silmeden önce uygunluk, kesinti ve negatif senaryoları doğrula. Genel başarısız analyzer dalına silme yetkisi verme.
3. Monitörde temizlik üst sınırının etkin runtime kaynağı eksik: API bu alanı okuyamadığı için software kapısı PARTIAL ve eski kod kap=0 gösteriyor. Fiili cap RC210 kaynakta5;0 bir çalışma ayarı değildir. İleride kanca kanıt fişine limit ve çalıştırma sonucu eklenmeli; monitör salt-okunur tüketmeli.
4.7 Kasım05:30 ve sonraki19:00–05:00 doğal akışı gözle.360 dosya sayısı frame-perfect veya mutlak UTC kanıtı yerine kullanılmamalı.
5. Mobile1.0.1 gerçek telefon kabulü; reboot/Range resume bakım kabulü; yönetici erişimi ile SMART; harici bildirim kanalı. Bunlar kanıtsız kapatılamaz.

## 6. Rollback ve teslim

G62 RC215: ~/.local/share/meteormesh/releases/rc215-final-audit-20261106. RC214 korunuyor.
Monitör geri alma: yalnız meteormesh-monitor.service.d/93-rc215-coverage-policy.conf kaldır, user daemon-reload, yalnız monitörü yeniden başlat.
Capture geri alma: yalnız meteormesh-capture.service.d/zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz-rc215-capture-lan.conf kaldır, user daemon-reload. Sonraki doğal tick eski ağ kapısına döner; devam eden kayıt servisine force-stop uygulanmaz.
manifest.json SHA kayıtlarını, live-evidence.json dağıtım sonucunu, audit-before.json kapsamlı canlı kesiti, integrity.json veri tutarlılığını içerir. check_changes.py altı yeni senaryoyu içerir. Önceden geçmiş testler topluca tekrarlanmadı.

Bu rapor yazılım olgunluğunu kanıtsız%99 ilan etmez. Önceki%12,5 değeri kabul puanıydı; gerçek kalan iş miktarı değildir.


## Ek canlı kabul — 6 Kasım 12:50 TRT

Doğal capture timer RC215 LAN helper üzerinden CAPTURE_LAN_READY verdi; Viva/Ruby/Surya DAY_IDLE ve CAPTURE_TICK: PASS, service success. Bu doğal gündüz entegrasyon kanıtıdır; Tailscale gerçekten kesilerek veya gece kaydı sırasında test yapılmadı.
