# MeteorAgent 1.2 — güvenli olay hafızası ve onarım sınırları

6 Kasım 2026, 20:10 TRT. Mevcut MeteorAgent, heartbeat-watch, OpsJournal ve status timerı kullanıldı. Yeni agent, timer, veritabanı veya telefon uygulaması kurulmadı.

## Yapılan düzeltme

OpsJournal'ın mevcut özel durum dosyasına en fazla 16 açık olay için ilk ve son görülme UTC zamanı ve en son 8 düzelme için düzelme UTC zamanı/süresi eklendi. Eski açık olayların ilk görülme zamanı bilinmediğinden geriye dönük süre uydurulmaz. Aynı olay sürerken yeni uyarı açılmaz. Agent mevcut olay kodu ve bilinen zamanları status raporunda gösterir. Bu alanlar salt küçük SSD JSON durumudur; videolara, HDD içeriğine veya SQLite'a dokunmaz.

Canlı Surya depolama olayı RC219'da zaten `PHONE_STORAGE_LOW` olarak sınıflandırılıyor. Mevcut hourly-pull beş dakikada bir doğrulanmış aktarım ve telefon purge yapıyor. Yeni Agent'ın bunun üzerine aynı servisi ayrıca başlatması çakışma yaratacağı için eklenmedi. Mevcut heartbeat-watch yalnız sertifika doğrulanmış Control-013 kanalında, taze heartbeat + HTTP arızası + uygun sürüm koşullarında `restart_listener` isteyebilir. 15 dakika cooldown ve saatte en çok iki deneme kodda mevcut; sonraki HTTP gözlemiyle sonucu `HTTP_REACHABLE_AFTER_REQUEST` veya `UNCONFIRMED_AFTER_TIMEOUT` olarak kaydeder. Android uygulaması tamamen durdurulmuşsa zorla başlatılmaz.

Surya'nın eski açık olayı için journal'da tek `ALERT` (5 Kasım 17:16:05 UTC), hiç `RECOVERED` olmadığı doğrulandı. Bu kanıtla yalnız bu olayın ilk görülme zamanı mevcut durum dosyasına işlendi; Agent canlı çıktıdaki `METEORAGENT_INCIDENT_OPENED` satırında gösterdi. Yeni veriden süre uydurulmadı. 6 Kasım 20:11 TRT kontrolünde Surya boş alanı 33,22'den 33,32 GiB'a yükselmişti; son aktarım üç segmenti `INGEST_VERIFIED` ve üç telefon purge fişiyle bitirdi. Uyarı, mevcut 3 GiB toparlanma marjı dolana kadar açık kalır.

## Kanıt ve sınırlar

Hedefli testte olay açıldı, yinelenmedi, 600 saniye sonra çözülme fişinin süresi 600 olarak hesaplandı. Kanıtsız eski olay için süre `null` kaldı. Önceki Control/camera geçiş ve tarihsel FAILED ayrımı regresyonu geçti. Üretim status override'ında her iki önceki `ExecStartPost` (heartbeat + OpsJournal) korundu. 17:11 UTC doğal status turu başarıyla tamamlandı; durum dosyası yeni alanlarla yazıldı. Daha sonra kanıtlı Surya başlangıcı eklendi.

Bu değişiklik Agent'ı genel komut çalıştıran bir aktöre dönüştürmez: Agent hâlâ `OBSERVE`; sınırlı listener kurtarması mevcut watcher'ın yetkisidir. Dış telefona anlık push teslimi etkin değil. Kayıt boşluğu ve düşük Surya alanı gibi sorunlarda kullanıcı müdahalesi gerektiğinde bunu ayrıca göstermek gerekir; gerçek telefonu uzaktan zorla uyandırma çözümü değildir.

## Dosyalar ve geri dönüş

G62 yayın: `~/.local/share/meteormesh/releases/rc220-agent-memory-20261106/`.

- `meteor-ops-local-alerts.py`: mevcut olay dosyasında sınırlı hafıza.
- `meteor-ops-journal.py`: aynı mevcut journal akışını sürümlü olay koduna yönlendirir.
- `meteor-status-agent.py`: bilinen olay zamanları raporu.
- `MeteorAgent-1.2/`: mevcut motor; RC219 teşhisi korunur.
- Drop-in: `~/.config/systemd/user/meteormesh-status.service.d/~rc220-agent-memory.conf`.

Rollback: yalnız `~rc220-agent-memory.conf` dosyasını kaldırıp `systemctl --user daemon-reload` çalıştır. Bir sonraki doğal status turu RC219'u ve önceki OpsJournal'ı kullanır. Yeni JSON alanları önceki kod tarafından yok sayılır. Kayıt, aktarım ve analizi yeniden başlatma gerekmez.

Ek hedefli düzeltme: CAMERA_NEEDS_REARM:<cihaz> olayı Agent'da PHONE_NEEDS_REARM olarak ayrı sınıflandırılır. Canlı telefona komut gönderilmedi; sınıflandırma testi PASS.
