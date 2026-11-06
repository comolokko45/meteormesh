# MeteorHub 0.9 — MeteorAgent Lite sabah raporu doğruluk düzeltmesi

**0.9, 0.8'in küçük ve geriye uyumlu geliştirmesidir.** Yeni zorunlu servis,
model veya Python paketi eklenmedi. Üç telefonun gece kaydı, sabah senkronizasyonu,
SQLite/tek işçi/dedektör ve otomatik silmenin kapalı olması değişmedi.

### 0.9 yenilikleri

- "Hangi kamera bu gece eksik kayıt yaptı?", "Ruby neden bağlantı hatası verdi?"
  ve "Surya offline mı?" gibi sorular geçmiş gecelerin toplam kamera envanteri
  yerine **son sabah çalışmasının** özetine yönlendirilir.
- Türkçe, kurala dayalı yanıt Ruby/Viva/Surya için varsa `OK`, `INCOMPLETE`,
  `NO_READY_SEGMENTS`, `OFFLINE_OR_ERROR` ve `SKIPPED_TIME_BUDGET` durumlarını
  açıklar. `OK`, gecenin tam kaydedildiğinin kanıtı olarak yorumlanmaz.
- Sabah raporundaki beklenmeyen kamera kimlikleri/durumları ve sayısal alanlar
  ayıklanır; raporun serbest hata metnindeki talimat benzeri veriler cevapta
  tekrarlanmaz. Rapor yoksa asistan eksik kamerayı bildiğini iddia etmez.
- Yalnız iki yeni regresyon testi eklendi; 0.9 `105/105` Python testini geçti.
  **Bu, fiziksel HP G62 veya Android kabul testi değildir.**

Kullanım (mevcut MeteorHub veri dizininizde):

```bash
python3 meteor_agent_lite.py --data-dir ./meteor-data ask "Hangi kamera bu gece eksik kayıt yaptı?"
```

Telefon veya Linux makinesinde gerçek sabah raporu yoksa cevap belirsizliği
korur. MeteorAgent Lite varsayılan `rules` modunda yerel LLM gerektirmez,
hiçbir araçla kayıt silmez ve ham videoya erişmez.

**Sürüm notu:** Aşağıdaki 0.8 bölümleri tarihsel açıklamalardır; buradaki
"0.4 APK derlenmedi" cümlesi artık son Android sürümünün durumunu belirtmez.
MeteorNode **0.5 debug APK** derlenmiştir ama üç telefonda saha testi bekler.

---

## Önceki 0.8 temel açıklaması

**0.8, MeteorHub 0.7'nin bağımsız kaynak sürümüdür.** Sabah indirme, tek MetDetPy işçisi ve salt-okunur MeteorAgent Lite akışı korunur. Yeni zorunlu servis veya Python bağımlılığı eklenmedi. **MeteorNode 0.4 hâlen APK olarak derlenmiş değildir.** Bunlar gerçek telefon/HP G62 kabul testleri değildir.

## 0.8 çift denetim sonuçları

1. Zamanı belirsiz bir adayın kendi kaynak MP4'ü, sidecar saat alanları eksik olsa bile olay bağlamında tutulur. Bilimsel UTC/eş-meteor doğrulaması yine **yok**.
2. Aday içeren video sonradan değiştirilmişse olay raporu üretilmez; MP4 SHA-256 tekrar sınanır. SQLite'da `INGEST_VERIFIED` ve başarılı dedektör çalışması bulunmayan kaynaklar rapora girmez.
3. `ffprobe` süresi `NaN`/sonsuz/0 veya video geometrisi geçersizse `INGEST_VERIFIED` sonucu verilmez. HTTPS host yapılandırmasındaki satır sonları reddedilir.
4. Önceki gecelerden kalan SQLite verisi, yeni çalışmada hiç READY videosu olmayan telefonu başarılı gösterebilirdi. Şimdi `NO_READY_SEGMENTS` ve `PARTIAL_REVIEW_REQUIRED` ile açık uyarı verilir; `new_night_completeness_proven=false`.
5. Kamera maskesi sadece PNG başlığından değil, gerçek ffmpeg decode testinden geçer. Deney sırasında video/maske/ayar hash'i değişirse sonuç yayımlanmaz.
6. İnsan etiketlemeleri yalnız provisional olay raporu şemasından kabul edilir; hiçbir etiket bilimsel kesinlik veya silme izni oluşturmaz.

Uygulamanın temel iş akışı değişmedi: üç telefon gece kayıt, sabah LAN indirme, yerel tek işçi analizi, geçici rapor. Otomatik Drive arşivi, gerçek PTS-UTC eşlemesi, gece kapanış kanıtı ve **otomatik silme uygulanmadı**. Üst klasörde `MeteorMesh-Cift-Denetim-v2.2.md` denetim tutanağı bulunur.

### 0.7'de yeni ve küçük beş işlev

1. `meteor_health.py`: Yerel SQLite'daki doğrulanmış video envanterinin süre/MB/son dedektör R özetini **salt-okunur** verir. Aynı özet `meteor_morning.py run` JSON'una `health_inventory` olarak eklenir. Veritabanı birden fazla geceyi tutabildiğinden **yalnız bu geceyi veya tam oturumu temsil etmez**. Kare PTS boşluğu ve UTC kesinliği ölçülmedikçe `UNVERIFIED` kalır.
2. `meteor_lab.py plan`: Mevcut MP4'ün ffprobe ile formatını ve isteğe bağlı maskenin PNG çözünürlüğünü doğrular. Maskenin SHA-256'sını raporlar. Orijinal videoyu değiştirmez.
3. `meteor_lab.py detect-sample`: Yalnız elle istenince **pinli MetDetPy v2.6.1** ile maske/hassasiyet/analiz boyutu deneyini yapar. Ayrı MDRF JSON ve `.audit.json` üretir; mevcut dosyanın üzerine yazmaz. Ana SQLite iş kuyruğunun yerine geçmez ve retention yetkisi yoktur.
4. `meteor_review.py`: Provisional event JSON'undaki tekil adaya `meteor_possible`, `aircraft_satellite`, `artifact`, `uncertain` etiketi ekler. Bağımsız, **append-only** `candidate-review.jsonl` kullanır; düzeltme önceki etiketi silmeden yeni satır ekler. Bu insan incelemesi bilimsel/retention onayı değildir.
5. `meteor_lab.py sample-frame`: Geceden **isteğe bağlı tek bir JPEG** örnek kare çıkarır; otomatik bulut sınıflandırıcısı veya ikinci video tarayıcısı çalıştırmaz.

### Kullanım örnekleri

```bash
# Veritabanı yoksa yalnız okuma için hata verir, bir şey oluşturmaz.
python3 meteor_health.py --data-dir ./meteor-data

# İndirilen gerçek MP4'te maske uyumluluğunu oku; çalıştırma yok.
python3 meteor_lab.py plan --video ./meteor-data/cam-ruby/SEGMENT.mp4 \
  --config ./m3det_normal.json --mask ./camera-masks/ruby.png

# İsteğe bağlı kontrollü karşılaştırma (gerçek MetDetPy v2.6.1 gerekli):
python3 meteor_lab.py detect-sample --video ./meteor-data/cam-ruby/SEGMENT.mp4 \
  --repo ./MetDetPy --config ./m3det_normal.json \
  --mask ./camera-masks/ruby.png --sensitivity high \
  --output ./ruby-masked-test.json

# Var olan provisional raporda candidate_id değerini okuyup etiketle:
python3 meteor_review.py label --data-dir ./meteor-data \
  --event-report ./meteor-data/morning-reports/events_provisional_EXAMPLE.json \
  --candidate-id aaaaaaaaaaaaaaaaaaaa --label uncertain

# Yalnız tek kare, bulut yüzdesi veya meteor hükmü vermez:
python3 meteor_lab.py sample-frame --video ./meteor-data/cam-ruby/SEGMENT.mp4 \
  --output ./ruby-sky-example.jpg --at-seconds 30
```

`SEGMENT`, `events_provisional_EXAMPLE` ve candidate ID yalnız yer tutucudur. Gerçek rapordaki kimlik **20 küçük harf hex karakterinden** oluşur. Önce Ruby 10–12 dk/HP testini yapmadan maskeleri üretim dedektörüne otomatik takmayacağız. Görüş açısı veya video crop değişirse eski maske geçersiz kabul edilir. `detect-sample`, `plan` ve insan etiketleme ana sabah otomasyonunun otomatik silme kararı oluşturmaz. **Auto-purge kapalıdır.**

Resmî özellik dayanakları: [MetDetPy detector mask/resize/flags](https://github.com/LilacMeteorObservatory/MetDetPy/blob/main/docs/tool-usage/Detector-usage.md) ve [Evaluate](https://github.com/LilacMeteorObservatory/MetDetPy/blob/main/docs/tool-usage.md). Güncel `evaluate.py` dokümanında bayrak farklılıkları olabildiğinden pinli checkout sürümünün `--help` çıktısı kullanılmalıdır; bu sürümde otomatik bilimsel `recall` hesabı **henüz eklenmedi**, çünkü etiketli gerçek referans veri yok.

---

## Korunan 0.6 temel açıklaması

Bu sürüm, **MeteorNode 0.1 read-only API'siyle geriye uyumlu** ve Android 0.2 kaynak koduyla eşleşecek şekilde tasarlanmıştır. Önceki 0.5 veri kurtarma/indirici, 0.4 MeteorAgent Lite, 0.3 kaynak/sınır ve 0.2 analiz katmanları korunur. **0.6 yeniliği:** `meteor_morning.py` ile sabah **telefonları sırayla çek → diskte rezerv koru → dosyaları doğrula → SQLite dedektör kuyruğunu yürüt → JSON raporla** zinciri eklendi. `systemd` örnekleri de dahil; **otomatik zamanlayıcı hiçbir cihaza kurulmadı veya etkinleştirilmedi**. Ayrıntılı HP G62 hazırlığı: `MORNING_SETUP.md`.

Sahte cihaz/dedektör testleri gerçek kayıt ve transferi kanıtlamaz. Sabah otomasyonu kapalı olmasa da `run` komutunu kullanıcı veya daha sonra testle açılan zamanlayıcı çağırır; varsayılan `plan` sadece okumadır. Sabah raporu MeteorAgent Lite'ın yedinci `morning_report` salt-okunur aracından görülebilir; AI/LLM kayıt, analiz, arşiv ve güvenliğin kritik yoluna sokulmadı.

İndirme katmanının üç görevi:

1. Linux CPU/RAM/disk ve işletim sistemi envanterini çıkarır.
2. `cam-ruby`, `cam-viva`, `cam-surya` telefonlarını **sırayla** sorgular.
3. Tamamlanmış MP4'leri HTTPS üzerinden indiren mevcut güvenli istemciyi çalıştırır, SHA-256 ve varsa `ffprobe` kontrolünden sonra durumları SQLite'a kaydeder.

**Yapmaz:** Android kayıt başlatma/durdurma, otomatik Drive yükleme, ham video silme, retention receipt, MCP, saat eşitleme, doğrulanmış bilimsel UTC, otomatik **Android gece kamera başlatma**. Linux sabah zamanlayıcısı örneği vardır fakat **kurulmuş/etkinleştirilmiş değildir**. MetDetPy gerçek HP G62 üzerinde henüz kurulup doğrulanmadı. MeteorAgent Lite modeli dahil değildir ve varsayılan model çağrılmaz.

## Gerekenler

- Linux ve Python 3.10 veya daha yeni sürümü.
- Kayıt dosyalarının tamamını tutmaya yetecek boş disk.
- `ffprobe` — dosyanın gerçekten açılabilir video olarak doğrulanması için. Kurulu değilse dosya hash ile doğrulansa da **`INGEST_VERIFIED` olarak işaretlenmez**.
- Telefonların aynı LAN'da çalışır halde olması ve önceden uygulama üzerinden kullanıcının başlattığı kayıtların READY durumuna gelmesi.
- `meteornode_pull.py` dosyası bu klasörde bulunmalı; pakete dahildir.
- Analiz için MetDetPy'nin resmî **v2.6.1** kaynak kodu ve bağımlılıkları ayrıca kurulmuş olmalı. Kaynak Git commit'i uygulama tarafından doğrulanır; farklı commit reddedilir.

## 1. Bilgisayar özelliklerini öğren

```bash
python3 meteormesh_hub.py inventory --output hub_capabilities.json
```

Mevcut bir `hub_capabilities.json` dosyasını **üzerine yazarak değiştirmez**. Yeni rapor için farklı bir dosya adı kullanın.

## 2. Üç telefonu tanımla

`devices.example.json` dosyasını **`devices.json`** olarak kopyalayıp gerçek yerel IP ve APK ekranındaki TLS sertifika SHA-256 parmak izlerini yazın. IP değerleri yalnız örnektir. `token_env` satırlarında **anahtarların kendisi değil**, Linux'taki ortam değişkeninin adı bulunur.

Token'ları kabuk geçmişine yazmamak için, her telefonu tek tek eşleştirerek:

```bash
read -rs -p "Ruby token: " METEORNODE_TOKEN_RUBY
export METEORNODE_TOKEN_RUBY
read -rs -p "Viva token: " METEORNODE_TOKEN_VIVA
export METEORNODE_TOKEN_VIVA
read -rs -p "Surya token: " METEORNODE_TOKEN_SURYA
export METEORNODE_TOKEN_SURYA
```

İlk etapta yalnız ruby kullanılıyorsa diğer iki girişi `"enabled": false` ile devre dışı bırakabilirsiniz.

## 3. Önce dosya indirmeden kontrol et

```bash
python3 meteormesh_hub.py check --config devices.json
```

Bu yalnız `GET /api/v1/health` ve `GET /api/v1/segments` çağrılarını yapar. HTTPS sertifika pin eşleşmesi doğrulanmadan Bearer token gönderilmez. İnternetten port açmayın.

## 4. Sabah indir

```bash
python3 meteormesh_hub.py sync --config devices.json --data-dir ./meteor-data
```

İşlem telefonları sırayla tamamlar. Her cihazın `meteor-data/cam-*/` dizininde `.mp4` ve `.json` dosyaları saklanır. Kesilen aktarım `.mp4.part` dosyasından yeniden denenir. `meteor-data/meteorhub.sqlite3` yalnız ingest durumudur; meteor bulunduğu ya da videonun silinebilir olduğu anlamına **gelmez**.

Durumlar:

- `INGEST_VERIFIED`: tam MP4 yerel SHA-256 eşleşmesi ve `ffprobe` medya kontrolü geçti.
- `HASH_VERIFIED_FFPROBE_MISSING`: hash doğru, ancak bilgisayarda `ffprobe` yok; eksik kontrol var.
- `FFPROBE_FAILED`: video kontrolü geçmedi; video telefondan veya Linux'tan silinmez.
- `ERROR`: bağlantı, dosya ya da doğrulama hatası; özgün dosya silinmez.

**Güvenlik ve mevcut sınır:** Burada otomatik silme kodu yoktur. Verinin Linux'a gelmiş olması temizleme izni üretmez. Üç telefonda gerçek ağ/MP4 testleri henüz yapılmadığından bu sürüm günlük tek dokunuşsuz işletim garantisi değildir.

## 5. Telefon olmadan hazırlanabilen analiz altyapısı

Yeni bir veri klasörü hazırlamak için (var olan 0.1 veritabanı varsa **silinmeden** yeni tablolar eklenir):

```bash
python3 meteormesh_pipeline.py --data-dir ./meteor-data init
```

MetDetPy'nin resmî deposunda `v2.6.1` sürümü için sabitlenmiş Git checkout ve paket bağımlılıkları ayrı hazırlanır. Uygulama `MetDetPy.py` dosyasını ve commit prefix'ini denetler. **Kurulu olmayan MetDetPy ile gerçek analiz yapılamaz.** Örnek `--metdet-config` gerçek upstream konfigürasyon dosyanızın yoludur; bu proje geçersiz bir örnek detector YAML'ı dağıtmaz.

Önceden güvenle indirilen (`INGEST_VERIFIED`) dosyaları SQLite kuyruğuna koy:

```bash
python3 meteormesh_pipeline.py --data-dir ./meteor-data enqueue \
  --metdet-config /path/to/MetDetPy/config/m3det_normal.json
```

Bir işi işlemek için (şimdilik kullanıcı başlatır):

```bash
python3 meteormesh_pipeline.py --data-dir ./meteor-data work \
  --metdet-repo /path/to/MetDetPy \
  --metdet-config /path/to/MetDetPy/config/m3det_normal.json --limit 1
```

Dedektör `MDRF` JSON raporu `meteor-data/detector-results/` altında üretilir. İşin input SHA-256'sı, config SHA-256'sı, detector sürümü, Git commit'i ve sonuç SHA-256'sı tutulur. Kaynak MP4'e yazılmaz. `work` hata verirse iş `FAILED` kalır; kaynak video korunur ve kullanıcı incelemesinden sonra şu komutla yeniden kuyruğa alınabilir:

```bash
python3 meteormesh_pipeline.py --data-dir ./meteor-data retry-failed
python3 meteormesh_pipeline.py --data-dir ./meteor-data status
```

Üç kamera için **kesin bilimsel eşleşme OLMAYAN**, yalnız inceleme amaçlı aday raporu:

```bash
python3 meteormesh_pipeline.py --data-dir ./meteor-data events \
  --devices cam-ruby,cam-viva,cam-surya --output ./meteor-events-review.json
```

Kaynak segmentin zamanları 0.1 APK'da **tahminidir**. Aday UTC'si çoğunlukla kaynak segmentin tahmini başlangıcı + (`start_frame/fps`) olarak hesaplanır. Kameralar arasında ±2 sn'lik yakınlık **aynı meteorun doğrulandığı anlamına gelmez**. Tek kameradaki adaylar listede kalır. Olay bağlamında üç cihazdaki `INGEST_VERIFIED` komşu segmentler **yalnız referans** olarak gösterilir. Preview oluşturma, 1–2 sn overlap ile dedektör yeniden çalıştırma ve tam gökyüzü konum eşleştirmesi henüz kodlanmadı. Zamanı bilinmeyen adayın yanında yalnız kendi kaynak segmenti sunulur.

`--output` var olan dosyayı **asla üzerine yazmaz**; her rapora yeni bir ad kullanın. Kayıt videolarının boşluk/PTS ölçümü gerçek telefon deneyi bekliyor.

## 6. MeteorAgent 0.3 temel gözlem araçları

```bash
python3 meteor_agent.py --data-dir ./meteor-data status
python3 meteor_agent.py --data-dir ./meteor-data cameras
python3 meteor_agent.py --data-dir ./meteor-data pending
python3 meteor_agent.py --data-dir ./meteor-data retention
```

Bu eski arayüz SQLite'ı `mode=ro` ve `PRAGMA query_only=ON` ile açar, dört sabit SELECT sorgusunu ve sonraki 0.3 rapor sorgusunu bilir. Jarvis tarzı sesli ajan, MCP sunucusu ya da serbest SQL/shell çalıştırıcısı değildir. 0.4'te bu sorgular yalnız izin listeli araç arayüzünden erişilir; **silme, upload, açık kabuk, HMAC secret ve retention onayı hiçbir durumda AI aracına verilmez**.

## 7. MeteorHub 0.3: segment sınırı incelemesi

Her telefonun beş dakikalık video dosyaları ayrı dedektör çağrılarına girdiğinde, sınırın çok yakınındaki kısa olayları kaçırma riski vardır. `boundary_review.py` aynı kameranın **yalnız `INGEST_VERIFIED`** durumundaki ardışık kayıtlarını yaklaşık duvar saati etiketlerine göre inceler. Başta hiçbir şeyi birleştirmez; ilk işlem sadece inceleme planı oluşturmaktır:

```bash
python3 boundary_review.py --data-dir ./meteor-data plan \
  --output ./sinir-plani.json
```

Plan, tahmini iki dosya aralığını ve birleştirmenin güvenli bir inceleme seçeneği olup olmadığını söyler. Çok büyük/belirsiz aralık varsa **birleştirmez**. İki kaydın arasındaki gerçekte kaybolmuş kareleri asla geri getiremez.

Bir sınır için, soldaki kaydın son 2 saniyesi + sağdakinin ilk 2 saniyesinden **analiz amaçlı yeniden kodlanmış**, orijinalin yerine geçmeyen MP4 üretmek:

```bash
python3 boundary_review.py --data-dir ./meteor-data build \
  --output-dir ./meteor-data/boundary-reviews --limit 1
```

Çıktı `b_*.mp4` ve buna bağlı `b_*.manifest.json` dosyasıdır. Kaynak MP4'lerin SHA-256'sı yeniden denetlenir, farklı çözünürlük reddedilir. Review MP4, `libx264 CRF14` ile yeniden kodlandığından **bilimsel orijinal değildir**. Orijinal videolar kaydedildikleri klasörde kalır. İnceleme klibinin geçiş noktası **yapaydır**; böylelikle görünen iz geçişi kanıtlanmış bilimsel süreklilik sayılmaz.

MetDetPy v2.6.1 kuruluysa bu kısa review MP4'leri de dedektöre göndermek mümkündür:

```bash
python3 boundary_review.py --data-dir ./meteor-data detect \
  --review-dir ./meteor-data/boundary-reviews \
  --metdet-repo /path/to/MetDetPy \
  --metdet-config /path/to/MetDetPy/config/m3det_normal.json --limit 1
```

Dedektör çıktısı + SHA-256 / sürüm bilgisi ayrıca yazılır. **Bu çağrının gerçek MetDetPy bağımlılıkları ile HP G62 üzerinde çalıştığı henüz doğrulanmadı.** Yapay örneklerden geçerli analiz dosyası üreten sahte çalıştırıcıyla arayüz test edildi.

## 8. Olay kaynağı indeksi — arşiv sanılmamalı

`meteormesh_pipeline.py events` komutunun ürettiği `meteor-events-review.json` girdisiyle olayın ilişkili orijinal MP4 referanslarını doğrulamak:

```bash
python3 event_package.py --data-dir ./meteor-data \
  --events ./meteor-events-review.json \
  --boundary-review-dir ./meteor-data/boundary-reviews \
  --output-dir ./meteor-data/event-indexes
```

Bu araç **tüm referans MP4'lerin SHA-256'sını yeniden kontrol eder**. Dedektör bir sınır incelemesinde aday bulmuşsa, normal segment dedektörünün aday bulmamasına rağmen onu **bağımsız inceleme kaydı** olarak tutar. Mevcut çıktıların üzerine yazmaz.

Çıktı yalnız JSON kaynak indeksidir. **Orijinal MP4'leri içine kopyalamaz, Drive'a arşivlemez, 60 saniyelik nihai klip oluşturmaz ve hiçbir silme yetkisi vermez.** Bu ayrım, henüz doğrulanmamış veriler yüzünden yanlışlıkla ham kayıt silinmesini önler.

## 9. Önceki deterministik MeteorAgent kısa durum raporu

```bash
python3 meteor_agent.py --data-dir ./meteor-data briefing
```

Bu komut kayıt sayısı, toplam boyut ve iş kuyruğu durumlarını Türkçe anahtarlı JSON biçiminde döndürür. Hâlâ **yapay zekâ modeli çalıştırmaz**; `mode=ro` ile salt okunur SQLite sorguları kullanır. Silme, komut yürütme, dosya indirme/yükleme veya kamera yönetimi yetkisi yoktur.

## 10. Mimari sınırlar ve gelecek kapılar

1. **Şimdi doğrulanan:** SQLite kuyruk idempotence, tek worker iş talebi, altı saat job lease, kontrol edilmiş sonuç JSON, geçici olay grupları, read-only ajan, ffmpeg ile sentetik segment sınırı çıkarma ve kaynak hash kontrolü. Bunlar çevrimdışı sentetik testlerdir.
2. **Gerçek telefon gelince:** Ruby envanter/10–12 dk kayıt, segment gap/PTS ve ekran kapanması; üç cihaz LAN/TLS/transfer testleri; gerçek timestamp ve LED deneyi.
3. **Gerçek video gelince:** HP G62 üzerinde MetDetPy v2.6.1 kurulum/uyum ve `R=işleme süresi/video süresi` testi, ardından segment sınırında 1–2 sn overlap analizi.
4. **En son:** SkyFit2 seçilmiş olay kalibrasyonu, gerçek kaynak/kısa klip paketleri, Drive doğrulanmış arşiv, gece kapanış bariyeri ve dry-run retention. Bunlardan önce otomatik purge yok.

**Sürüm uyumu:** Hub 0.7, eski Hub sürümlerini ve MeteorNode 0.1 APK/0.2/0.3 kaynaklarını değiştirmez. MeteorNode 0.3 ayrı kaynak kodudur; 0.3 APK henüz derlenmedi.

## Yazılım testleri

```bash
python3 -m unittest discover -v
```

Bu pakette ilk sürümlerin testleri, 0.3'ün ffmpeg testleri, 0.4'ün araç/ajan güvenlik testleri, 0.5'in kurtarma ve 0.6'nın sabah otomasyon testleri birlikte çalışır. `ffmpeg/ffprobe` varsa sentetik MP4 inceleme klibi üretilir. **Gerçek cihaz aktarım hızı, yerel model performansı ve meteor duyarlılığı ölçülmedi.**

## 11. MeteorAgent Lite 0.4 — küçük, yerel, denetlenmiş uzman

0.4 bir meteor dedektörü değildir. Videoyu yine **MetDetPy** analiz eder; ajan yalnız kayıt/analiz verilerini ve kısa, kaynaklı astronomi bilgisini yorumlar. Basit Türkçe sorular için hiç LLM gerekmeyen `rules` modu vardır.

**Güvenli araçlar (0.7'de 8):** `station_status`, `camera_summary`, `job_queue`, `retention_status`, `daily_briefing`, `knowledge_search`, `morning_report`, `recording_health`. Son araç yalnız yerel geçmiş envanterini okur; canlı kamera kontrolü veya purge yoktur.

Araç çağrılarında en çok **3 çağrı**, sıkı isim/argüman izin listesi ve salt-okunur ana SQLite kullanılır. Ajan geçmişi yalnız ayrı `meteoragent-history.sqlite3` dosyasına yazılır. MeteorHub ana veritabanı model tarafından yazılamaz. Her türlü istemden bağımsız olarak modelin shell, internet upload, telefon komutu, silme, HMAC/receipt oluşturma veya retention anahtarına erişim kanalı yoktur.

Önce ana veritabanını (ve kuyruk tablolarını) hazırla:

```bash
python3 meteormesh_pipeline.py --data-dir ./meteor-data init
python3 meteor_agent_lite.py --data-dir ./meteor-data briefing
python3 meteor_agent_lite.py --data-dir ./meteor-data ask "Günlük istasyon raporu"
python3 meteor_agent_lite.py --data-dir ./meteor-data ask "Meteor ile meteorit arasındaki fark nedir?"
```

Bunlar **internetsiz, modelsiz** çalışır; `rules` varsayılandır. `knowledge_search` kartları kaynak adını ve varsa kaynak bağlantısını taşır. Kelime eşleşmesi küçük ve açıklanabilir; büyük semantik arama veya model fine-tuning yoktur.

Yerel LLM kurulmadan araç-döngüsü sınama:

```bash
python3 meteor_agent_lite.py --data-dir ./meteor-data --provider mock \
  ask "Ruby kamerasının durumunu incele"
```

`mock` gerçek AI değildir; otomatik izinli araç çağrısının sahte testidir. Çıktıda **SİMÜLASYON** yazar.

Yerel model yalnız kullanıcının kararıyla, gerçek HP G62 RAM/CPU/CPU komut seti envanterinden ve analiz performansı testinden sonra kurulmalıdır. Uygun bulunursa resmî Ollama `qwen3:0.6b` modeli seçilebilir. Model ayrı kurulup Ollama localhost servisi başlatıldığında:

```bash
python3 meteor_agent_lite.py --data-dir ./meteor-data --provider ollama \
  --model qwen3:0.6b ask "Bu gece analiz kuyruğunda sorun var mı?"
```

Bu sürüm HTTP'yi yalnız **127.0.0.1:11434** üzerinde kullanır; dış bağlantıya, kullanıcı tarafından verilen URL'ye veya proxy'ye izin vermez. Kaynak tasarrufu için düşünme modu kapalıdır; model son kullanımdan yaklaşık bir dakika sonra bellekten çıkar. Modelin küçük olması Türkçe bilimsel yorumlarının otomatik doğru olduğu anlamına gelmez. Telefonlar veya HP G62 ile gerçek deneme, yanıt hızı, bellek ölçümü, model tool-call doğruluğu yapılmadı; bu bir **hazırlanmış adaptördür**, üretim onayı değildir.

**Otonomi sınırı:** Sabah ingest/analiz ve durum raporu için deterministik çalışma komutu ve `systemd` **örnek** hizmeti 0.6'da hazırlandı; gerçek HP'de etkinleştirilmedi. Model bir sonraki video analizini serbestçe başlatamaz. RAG için büyük veritabanı veya LLM katmanları zorunlu değildir. Telefonları uzaktan başlatma, Drive, silme, gerçek meteor sayısı ve sesli Jarvis bağlantısı henüz yoktur.

## 12. MeteorHub 0.6 — otomatik sabah akışını hazırlama

Önce hiçbir dosya indirmeden planı inceleyin:

```bash
python3 meteor_morning.py plan --config devices.json --data-dir ./meteor-data \
  --metdet-repo /path/to/MetDetPy --metdet-config /path/to/detector.json
```

Gerçek telefon ve HP kabul testleri geçince aynı komutun `run` aşaması, telefonları sırayla indirir, 2 GiB yerel disk rezervi korur, SHA-256+JSON+ffprobe kontrolü yapar, SQLite kuyruğunu idempotent günceller, tek MetDetPy işçisi çalıştırır ve rapor oluşturur. Diğer telefona geçmek için bir cihazın çevrimiçi olması zorunlu değildir. Aynı anda ikinci sabah çalışması dosya kilidiyle engellenir.

```bash
python3 meteor_morning.py last-run --data-dir ./meteor-data
python3 meteor_agent_lite.py --data-dir ./meteor-data ask "Sabah aktarım özeti"
```

**Tam kurulum/komut/timer rehberi:** [MORNING_SETUP.md](MORNING_SETUP.md). Bu sürümde gecenin tamamlandığını söyleyen bir `SESSION_ANALYSIS_CLOSED` kanıtı, Drive arşivi, silme izni veya doğrulanmış meteor sayısı **yoktur**. `COMPLETED_PROVISIONAL` yalnız bilinen işleri bitirme durumudur.

**Kaynaklar:** [Ollama araç kullanımı](https://docs.ollama.com/capabilities/tool-calling), [Qwen3 0.6B](https://ollama.com/library/qwen3:0.6b), [NASA meteor](https://science.nasa.gov/solar-system/meteors-meteorites/), [GMN](https://globalmeteornetwork.org/wiki/), [MetDetPy](https://github.com/LilacMeteorObservatory/MetDetPy).
