# MeteorMesh 0.8 — sabah otomasyonunu HP G62'de hazırlama

Bu belge **gerçek HP G62 ve telefonlarda henüz çalıştırılmamış** kurulum yönergesidir. Linux 0.8 kaynak sürümü hazırlanmıştır; Android 0.4 kaynakları hâlâ ayrıca derlenip cihazda sınanmalıdır.

## Ne yapar, ne yapmaz?

- Aynı LAN'daki üç telefonu **sırayla** yoklar, yalnız READY MP4'leri güvenli HTTPS/pin ile çeker.
- Gelen dosya SHA-256 + metadata + `ffprobe` kapısından geçmeden `INGEST_VERIFIED` olmaz.
- SQLite'da iş kuyruğunu tekrar etmeden günceller, **tek MetDetPy işçisi** yürütür; süre ve disk rezervi vardır.
- Kaynak 5 dk MP4'leri ve eski raporları değiştirmeden yeni JSON durum raporu üretir.
- Bir telefon çevrimdışı ise diğerlerini denemeye devam eder, sonucu `PARTIAL_REVIEW_REQUIRED` olarak kaydeder.
- Ajanın `morning_report` aracı yalnız son raporu **okur**.
- Otomatik Drive upload, gerçek meteor doğrulaması, gece kapanış bariyeri, otomatik silme, Android kamerayı uzaktan başlatma, serbest AI/shell **yapmaz**.

## 1. HP G62 kabul envanteri

Bilinen donanım: Intel i5-460M (2 çekirdek/4 izlek, AVX yok), 8 GB RAM. Disk ve Linux/CPU paket uyumu henüz bilinmiyor. İlk olarak gerçek HP üzerinde:

```bash
python3 meteormesh_hub.py inventory --output hub_capabilities.json
python3 --version
ffprobe -version
df -h
```

`inventory` var olan dosyanın üzerine yazmaz. MetDetPy v2.6.1 gerçek kaynak checkout'u, Python modülleri ve test MP4 ile ayrıca kurulup doğrulanmalıdır. Yanlış checkout/eksik paketle detector çalıştırılmaz; iş kuyruğu ve ham video korunur.

## 2. Güvenli `plan` komutu — dosya indirmez

Önce `devices.example.json` dosyasını, kullanıcıya ait ayrı `devices.json` olarak düzenleyin. Telefonda görünen eşleştirme token'larını bu JSON'a koymayın. Her cihaz için token değeri, JSON'daki `token_env` ismiyle Linux ortamında bulunmalıdır.

```bash
python3 meteor_morning.py plan \
  --config ./devices.json \
  --data-dir ./meteor-data \
  --metdet-repo /ABSOLUTE/PATH/MetDetPy \
  --metdet-config /ABSOLUTE/PATH/meteor-detector-config.json
```

Bu komut telefon, SQLite, video veya dosya arşivine yazmaz. `ffprobe_found`, detector yolları ve planlanan cihazları bildirir.

## 3. İlk deneme — zamanlanmamış manuel çalışma

Eşleşme token'larını kabuk geçmişine yazmadan ortam değişkenlerine yükleyin. Gerçek örnekleri `devices.example.json` içindeki `token_env` alanlarında görürsünüz. Burada sır paylaşmayın.

```bash
python3 meteor_morning.py run \
  --config ./devices.json \
  --data-dir ./meteor-data \
  --metdet-repo /ABSOLUTE/PATH/MetDetPy \
  --metdet-config /ABSOLUTE/PATH/meteor-detector-config.json \
  --max-jobs 288 --max-runtime-hours 12 \
  --detector-timeout-seconds 1800 --min-free-gb 2
```

`morning-reports/morning_*.json` raporları yeni isimle eklenir. `COMPLETED_PROVISIONAL` **bilimsel doğrulama değildir**; sadece bu denemedeki aktarım ve iş kuyruğu aşamalarında belirgin bir başarısızlık olmadığını ifade eder. `PARTIAL_REVIEW_REQUIRED` olduğunda `errors`, `queue.jobs`, telefon durumlarına bakın. Otomatik purge yine `false`.

**0.8 uyarısı:** Çevrimiçi bir telefon sıfır READY video döndürürse `NO_READY_SEGMENTS` işaretlenir; önceki gecelere ait SQLite kayıtları başarılı yeni gece sayılmaz. Güncel gece için uçtan uca `capture_session` kapanış kanıtı henüz yoktur.

Son raporu okumak için:

```bash
python3 meteor_morning.py last-run --data-dir ./meteor-data
python3 meteor_agent_lite.py --data-dir ./meteor-data ask "Sabah aktarım özeti nedir?"
```

Model varsayılan olarak çalışmaz; `rules` modu kullanılır.

## 4. systemd zamanlayıcı — **yalnız gerçek cihaz testlerinden sonra**

`systemd/meteormesh-morning.service.example` ve `.timer.example` örnektir; bu paketin kurulması hiçbir zamanlayıcıyı etkinleştirmez.

1. Hizmet örneğindeki bütün `REPLACE_WITH_ABSOLUTE_*` yollarını gerçek HP yollarıyla değiştirin.
2. Kullanıcı için `~/.config/meteormesh/tokens.env` oluşturup `METEORNODE_TOKEN_RUBY=...`, `...VIVA=...`, `...SURYA=...` değerlerini yazın. Yalnız ilgili kullanıcı okuyabilsin: dizin `0700`, dosya `0600`. Token dosyasını GitHub/Drive'a yüklemeyin.
3. İki üniteyi kullanıcı `~/.config/systemd/user/` dizinine sırasıyla `meteormesh-morning.service`, `meteormesh-morning.timer` adıyla koyun.
4. Önce `systemctl --user daemon-reload` ve `systemctl --user start meteormesh-morning.service` ile **tek manuel tam prova** yapın. Günlüğü `journalctl --user -u meteormesh-morning.service` ile gözleyin.
5. Her şey doğrulandığında `systemctl --user enable --now meteormesh-morning.timer` ile 09:00 (PC yerel saatine göre) günlük akışı açabilirsiniz.

`Persistent=true`, makine kapalıyken kaçırılan takvimi uygun koşullarda sonraki etkinleşmede yakalar. Bu özellik **bilgisayarı uyandıracağını garanti etmez**; kullanıcı oturumu, uyku politikası, ağı ve saat ayarı ayrıca doğrulanmalıdır. Geceleri kamera kaydı kullanıcı tarafından önceden başlatılacak. Android 14/15+ foreground kamera kısıtları atlatılmaya çalışılmaz.

## Kritik kapasite sınırları

- 6 Mbit/sn × 8 saat × 3 telefon ≈ **64,8 GB/gece**; bu, yerel video aktarımı ve analizden önce gerekli geçici kapasitedir.
- 2 GiB disk rezervi yalnız **kesilen transfer yüzünden diskin sıfıra gitmesini azaltan durdurma eşiğidir**; 65 GB gecelik giriş için yeterli depolama kapasitesi değildir.
- `max-runtime-hours`, tek bir büyük dosyanın ortasında transferi zorla kesmez; dosya bitince sıradaki işe geçmeden kontrol eder. 24 saatlik video işleme süresi HP i5/8GB üzerinde **ölçülmedi**.
- MetDetPy iş durumu başarısız kalırsa geçmiş kaynaklar saklanır ve manuel inceleme/`retry-failed` kararı beklenir.
- Sadece tamamlanmış analiz + Drive arşiv doğrulaması + `SESSION_ANALYSIS_CLOSED` + kullanıcının retention policy onayı ileride güvenli auto-purge için temel olabilir. Bu aşamalar 0.6'da **yok**.

Resmî zamanlayıcı davranışı: https://freedesktop.org/software/systemd/man/latest/systemd.timer.html
