# MeteorMesh otopilot — güncel çalışma kaydı

5 Kasım 2026, 23:20 TRT. Bu kayıt canlı sistem ve hedefli testlere dayanır; doğal gece kabulü yerine geçmez.

1. **Bağımsız durum takibi — PARTIAL:** HTTP, Control, ADB ve kamera durumu ayrı gösteriliyor. RC203 eski kaynak eksikliklerini güncel geceden ayırır. Son monitör örneğinde üç telefon HTTP/Control çevrimiçi ve kayıt yapıyor; ADB üçünde kopuk, bu kayıt arızası sayılmıyor. Segment sınırlarının kesinliği henüz kanıtlı değil.
2. **Sınırlı güvenli toparlama — PARTIAL:** RC202, 0.13.6 için izinli listener toparlamasını ve tekrar sınırının korunmasını düzeltir. RC205 sonraki HTTP gözlemini kaydeder; bu gözlem komutun arızayı giderdiğinin kesin kanıtı değildir. İzole testler geçti; gerçek arıza altında sonuç henüz doğrulanmadı. RC200 yerel ağ kapısı yalnız hazırlıkta, aktif kayıt sırasında uygulanmadı.
3. **Depolama ve kuyruk — PARTIAL:** RC201, Surya 32 GiB rezervine yaklaşınca tek olay açar ve histerezis uygular. Son örnek 33,46 GiB; uyarı açık, rezerv henüz aşılmamış. RC203 güncel gece ile geçmiş 99 eksik kaynak işini ayırır; tarihsel 119 FAILED yeni gece başarısızlığı sayılmaz. Uzun dönem analiz kapasitesi ve gerçek gece etkisi henüz kabul edilmedi.
4. **Güvenli temizlik — PARTIAL:** Kullanıcı hem tek dosya pilotunu hem otomatik negatif temizliği onayladı; yeniden onay istenmeyecek. RC207 kamera kayması için aday korumasını genişletti; RC208 kayıt penceresi ve telefon kayıt durumunu silme kapısına ekledi. RC210 yedek fişinin gerçekten doğrulanmaması açığını kapattı. Negatif dosyalar için üç kamera aralığı/UTC kesinliği henüz kanıtlı değil; daha önce önerilmiş Viva dosyası eksik Surya kapsaması nedeniyle güvenli değil. Politika kapalı, HDD silinmedi; kayıt sürerken gerçek pilot yapılmaz. Uzaktan telefonun eşzamanlı kayıt başlatmasıyla sağlık kontrolü arasındaki yarış ayrıca çözümlenmeden kontrol mutlak garanti değildir.
5. **Olay, sonuç ve monitör — PARTIAL:** RC204 açık OpsJournal olaylarını Agent değerlendirmesine, RC206 salt okunur monitörün genel uyarılarına taşır. Surya uyarısı gerçek veride tek açık olaydır. RC205 toparlama sonrası gözlemi saklar. Olaylar yereldir; telefona harici push teslimi mevcut olduğuna dair kanıt yoktur. Veri kaynağı yaklaşık beş dakikada güncellenebilir.

## Bu turdaki RC210 düzeltmesi

Kök neden: yedek doğrulayıcı `DB_BACKUP` etiketini denetliyordu, çağrı `DB_BACKUP_PRE_RETENTION` kullanıyordu. Başarı çıkışı yanında doğrulama fişi de artık tam satır olarak zorunlu; yoksa retention atlanır. Sahte/eksik fiş, başarısız çıkış, doğru fiş ve retention atlama testleri G62 izole testlerinde geçti. Canlı ayar bir sonraki analiz sonrası çağrıya yönlendirildi; doğal başarılı analiz akışı henüz gözlenmedi. Release: `~/.local/share/meteormesh/releases/rc210-backup-proof-20261105`. Rollback: aynı release altındaki `rollback/rc208-dropin.conf` dosyasını analyzer drop-in dizinindeki mevcut `*-rc208.conf` yerine koyup `systemctl --user daemon-reload` çalıştırmak; kayıtları veya analiz servisini yeniden başlatmak gerekmez.

## Bekleyen işler ve sınırlar

- Gece sonunda 05:00, 05:30 ve 09:00 işlemlerinin gerçek kapsam ve sonuçlarını değerlendir; üç kamera ve segment sürekliliğine kanıtsız PASS verme.
- Üç kamera zaman örtüşmesi ve ilgili olay koruması kanıtını tamamla; kayıt dışında yeni bir uygun aday için onaylanmış gerçek tek dosya pilotunu çalıştır, fiş/tombstone ve yeniden indirme davranışını doğrula. Ancak bundan sonra onaylı otomasyonu etkinleştir.
- RC209, dolu geçen turdan sonra sınırlı ikinci beşlik tur fikrini yalnız izole testte gösterir; üretime uygulanmadı. Gerçek işlem süresi, kilit çakışması ve güvenlik kanıtı ölçülmelidir. 9 analiz/tur karşısında 5 silme/tur birikme oluşturabilir.
- Gerçek bağlantı kesintisi sonrası toparlama, G62 yeniden başlatma ve birden fazla doğal geceyle operasyonel güvenilirlik kabulü bekliyor. Yüzde 99 için ölçüm yok.
- Surya'nın boş alanını ve disk sağlığını izle; tek HDD'de kalan kayıtların bağımsız yedeği yoksa donanım kaybı riski sürer.
