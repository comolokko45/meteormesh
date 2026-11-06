# MeteorAgent 1.2 — tanı doğruluğu geliştirmesi RC219

6 Kasım 2026, 20:05 TRT. Yeni agent veya timer oluşturulmadı.

Kök neden: Mevcut Agent, OpsJournal'daki `STORAGE_LOW:surya` olayını yalnız `LOCAL_ALERTS_OPEN` olarak yazıyordu. Gerçek risk kullanıcıya Agent düzeyinde açık görünmüyordu. Mevcut Agent karar motoru, aynı yerel olayları cihaz depolaması, kayıt durması, HTTP API kaybı, Control heartbeat eskimesi, aktarım ve analiz hatası olarak sınıflandıracak şekilde dar kapsamda güncellendi. Tanınmayan olaylar eski genel uyarıda kalır; ADB durumundan MeteorNode OFFLINE sonucuna atlanmaz.

Yeni nedenler yalnız teşhis ve öneridir. Agent `OBSERVE` modunda, yan etkili işlem ve zamanlayıcı yönetimi kapalıdır. Mevcut heartbeat-watch, OpsJournal, capture, transfer ve analiz servislerinin eylem sınırları değişmedi.

Hedefli test: Surya depolama, Ruby API, Viva heartbeat, kayıt durması, aktarım/analiz hata ayrımı ve tanınmayan kodu koruma PASS. Canlı G62 status girdisiyle çıktı `PHONE_STORAGE_LOW`; Surya kodu ayrı satırda gösterildi. Üretim systemd drop-in'i aktif; zamanlayıcının kendi doğal kabulü ayrıca izlenecek.

Kaynak: `MeteorMesh-RC219-Agent-Diagnosis-2026-11-06/`. G62 yayın: `~/.local/share/meteormesh/releases/rc219-agent-diagnosis-20261106/`. Drop-in: `~/.config/systemd/user/meteormesh-status.service.d/90-rc219-agent-diagnosis.conf`. Rollback yalnız bu drop-in'i kaldırıp `systemctl --user daemon-reload` çalıştırmak; mevcut servis/timer yeniden başlatılmaz.

Sonraki geliştirme sırası: (1) olayların ilk/son görülme süresi ve geri dönüşünü Agent raporuna bağla; (2) mevcut güvenli komutlar için açık önkoşul, en fazla bir deneme, cooldown ve sonuç fişi ekle; (3) Surya alanı düşerken doğrulanmış aktarımın gerçekten alan kazandırdığını izle; (4) Android yeniden hazırlama gerektiğinde `NEEDS_REARM` bildir. Riskli silme, sistem reboot veya serbest komut yetkisi Agent'a verilmemeli. Gerçek dış bildirim teslimi hâlâ yapılandırılmadı.
