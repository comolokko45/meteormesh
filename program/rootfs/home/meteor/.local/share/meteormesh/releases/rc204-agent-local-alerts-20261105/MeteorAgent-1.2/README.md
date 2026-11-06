# MeteorAgent AutoPilot 1.2 — NightScope-aware güvenli çalışma değerlendirmesi

**30 Ekim 2026 — izolasyon geliştirmesi, üretime kurulmadı**

MeteorAgent 1.0'daki aynı JSON okuyucu, hata sınıflandırıcısı ve yetki ret politikasını korur. Yeni sürümde sadece **tek ek dosya** `agent_review.py` vardır. İkinci veri tabanı, zamanlayıcı, servis, LLM, telefon istemcisi, indirme veya analiz motoru eklenmedi.

## Yeni davranış

Mevcut `MeteorMesh-status-Hub09.py` tarafından üretilen `meteormesh.local-status.v1` JSON'undan, var olan Hub iş sayıları ile birlikte, tek bir operatör sonraki adımı çıkarır:

- Geçersiz/eski kanıt → `UNKNOWN`, işlem önerilmez.
- HDD UUID/mount geçersiz → `BLOCKED`; kuyruk hatası ikincil sayılır.
- Sabah zamanlayıcısı aktif veya durumu belirsiz → `BLOCKED`.
- SQLite okunamıyor → `BLOCKED`.
- `FAILED` işler → `REVIEW`, **otomatik retry yok**.
- Bekleyen/çalışan işler → `OBSERVE`, **yeni kuyruk/worker yok**.
- Diğer durumlar → `OBSERVE`; "gece tamamlandı" anlamı taşımaz.

Yalnızca konsol çıktısı üretir. Bütün yan etkili eylemler devre dışıdır.

## İzole test

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests python3 -m unittest discover -s tests -v
```

Örnek giriş (sentetik, geçmişte oluşturulmuş):

```bash
PYTHONDONTWRITEBYTECODE=1 python3 meteor_agent_cli.py --input examples/offline-disk-example.json --format json
```

Mevcut status aracıyla gelecekte tek seferlik bağlantı **ancak MM-01 ve status aracının SQLite `mode=ro` / WAL yardımcı dosyaları davranışı doğrulandıktan sonra** yapılmalı. Bu sürüm status aracını kendi başına çağırmaz. `meteormesh-morning.timer` etkinleştirilmez.

## Kanıt ve açık sınırlar

- Bu paketin **53 otomatik testi**, bilgisayara bağlı olmadan sentetik JSON ile çalışır.
- HP G62, canlı Mongo/SQLite, gerçek disk veya MP4 üzerinde test yapılmadı.
- MM-01 üretim koruması hâlâ **AÇIK**; önceki 33 testli bağımsız `MeteorMesh-MM01-Direct-v0.1` paketi üretime entegre edilmiş sayılmaz.
- `meteormesh-status.timer` zaten var: yeni timer oluşturulmadı.
- `Agent 1.1` gerçek iş başlatma, retry, silme, uzaktan kamera kontrolü, ağ veya servis yapılandırması YAPMAZ.

## Bir sonraki gerçek kod işi

MM-01 ortak `StorageGuard`'ını MeteorHub 0.9'ın `meteor_morning.py`, `meteormesh_hub.py`, `meteormesh_pipeline.py`, `meteornode_pull.py`, `hub_workflow.py` dosyalarında her üretim yazma yoluna bağlamak ve eski 105 testiyle birlikte negatif mount/UUID testlerini yürütmek. Bunu yapmadan ajan `assisted` seviyesine çıkarılmayacak.
