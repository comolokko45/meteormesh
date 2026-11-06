# RC198 — Negatif video kamera ve segment kapsamı

5 Kasım 2026'da kullanıcı negatif videolar için sürekli otomatik silmeyi ve ayrı tek dosyalık pilotu onayladı. Kullanıcının kayıt penceresinde veri silmeme talimatı korunur. Politika şu anda kapalıdır; pilot ve sonrasında sonuç denetimi henüz yapılmadı.

RC193 yalnız ±315 saniye içinde her kamera için başarılı analiz bulunmasını kamera kapsamı sayıyordu. Bu, bir kameradaki sonraki beş dakikalık kaydı, eksik mevcut kayıt yerine sayabiliyordu. Gerçek Viva `viva_20261104T165024Z_8e766cadf2b8` dry-run'da uygun görünmesine rağmen Surya'nın aynı zamanlı ve önceki segmentleri eksikti. Bu dosya pilot seçeneğinden çıkarıldı ve korunuyor.

RC198 önceki, mevcut ve sonraki 300 saniyelik aralık için üç kameranın her birinde hedef başlangıcın 60 saniye içinde başarılı analiz bulunmasını zorunlu kılar. Aday ve belirsiz sonuçların ±315 saniye koruması, pending kontrolü, SHA, HDD UUID, tombstone ve sınırlandırılmış dry-run korunur. Bu başlangıç-zamanı eşleştirmesi korumayı artıran muhafazakâr bir filtredir; tahmini UTC doğruluğunu veya tüm dosyanın gerçek zamanlı kapsandığını tek başına kanıtlamaz. Özellikle eksik komşular ve kamera kayıpları nedeniyle uygun negatif dosyalar da korunabilir.

Staging hedefli testleri, dört mevcut aday/belirsiz sonuç/pending/eksik kamera regresyonu ve gerçek Viva segmentinin `camera_coverage_incomplete` ile reddi PASS. Genel dry-run 10 eksik kapsamlı kaydı reddetti ve `cam-ruby/ruby_20261104T170026Z_aa29860c7bdc` kaydını bir aday olarak raporladı; bu dosya da gerçek pilot öncesi ve kayıt penceresi dışında yeniden doğrulanmalıdır. Gerçek silme yapılmadı.

G62 release: `~/.local/share/meteormesh/releases/rc198-retention-coverage-20261105`; analyzer drop-in `*z-rc198.conf` yalnız `ExecStopPost` yolunu değiştirir. Policy `auto_delete_enabled=false`; RC195 analiz batch ve RC194 saat kapısı korundu. Kaynak SHA-256 `ad30db474384a1c794cf609599c71369ce4fc7a9ab6deeba4b35cce694cce977`. Doğal analiz sonrası RC198 dry-run henüz gözlenmedi.

Geri alma: yalnız analyzer `*z-rc198.conf` drop-in'ini kaldırıp `systemctl --user daemon-reload` çalıştır; RC193 post-hook geri gelir. Silme marker'ını kapalı tut. Hiçbir video veya SQLite disposition kaydı değiştirilmedi.

Kapasite: mevcut otomatik post-hook en çok 5 silme/tur, analyzer en çok 9 iş/tur. Her iş negatif çıkarsa birikim oluşabilir. Sınır ölçüm ve güvenlik değerlendirmesi olmadan yükseltilmedi; telefon pull gecikmesi, hash süresi ve mevcut lock birlikte ölçülerek çözüm geliştirilmeli.
