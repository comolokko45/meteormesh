# MeteorMesh Monitor — RC187

Salt-okunur, Turkce, standart Python ve HTML/JavaScript kullanan yerel durum ekrani.

## G62 uzerinde

- Servis: meteormesh-monitor.service (systemd user service)
- URL: http://127.0.0.1:8765
- Dinleme: yalniz 127.0.0.1; dis aga/firewall'a port acmaz.
- Veri: mevcut RC180 meteor-ops-summary.py --preview, read-only SQLite, /proc, findmnt, systemd timer listesi, analiz NightScope dosyasi ve son morning raporlarindan gelir.
- On bellek: 45 saniye. Tarayici yenilemesi 30 saniyede bir. Ham MP4 taramasi/cozmesi yapilmaz.
- Arayuzde yazma, telefon kontrolu veya silme dugmesi yoktur. HTTP POST/PUT/DELETE 405 dondurur.
- Control-013 8443'e, kayit/analiz servislerine veya zamanlayicilara mudahale etmez.

## Windows uzerinden guvenli erisim

Tailscale acik olan Windows bilgisayarda tunel kurun:

    ssh -N -L 8765:127.0.0.1:8765 meteor@100.113.204.5

Ardindan http://127.0.0.1:8765 adresini acin. Kaydedilmis SSH anahtari kullanmak icin Open-MeteorMesh-Monitor.ps1 dosyasini calistirabilirsiniz. Tuneli kapatmak icin Stop-MeteorMesh-Monitor-Tunnel.ps1 kullanin.

G62'de yerel grafik masaustu/tarayici bu denetimde bulunmadi; monitor SSH tuneli ile Windows tarayicisinda kullanilabilir.

## Kaynak tazeligi ve sinirlar

- Telefon health degerleri mevcut heartbeat watcher snapshot'indan gelir; ADB, HTTP API ve Control-013 ayri sunulur.
- VERI GUNCEL DEGIL durumu normal/yesil gosterilmez.
- Aktif kayit gecesi saat 19:00–05:00 Europe/Istanbul penceresinden hesaplanir; rapor scope'undan bagimsizdir. Tum gecenin SQLite segmentleri, her segmentin analiz durumu ve ilk/ara/son araliklari gosterilir; G62'ye henuz aktarilmamis segmentler ayri sayilir.
- Aktif capture gecesi kuyrugu, analiz scope dosyasi ve son rapor scope'u yan yana gosterilir. Eslesmiyorsa analiz basladi varsayilmaz; gece isleri WAITING olarak kalir.
- Telefonun kamerasi RECORDING demek tek basina kesintisiz kayit kaniti degildir; timeline baslangic zamanlari ve ffprobe surelerinden turetilir.
- OpsJournal acik uyarilari gosterilir. Mevcut OpsAlert state dosyasi kapatilmis toparlama fiillerini saklamadigindan gecmis mudahale sayisi uydurulmaz.
- Aday ciktisi bilimsel olarak dogrulanmis meteor degildir.
- 7/30 gece istatistigi yalniz son 30 rapor ornegi icinden gece kimliklerini sayar; tam arsiv yuzdesi olarak sunulmaz.
- HDD icin onceki kanonik kayitta 446 yeniden eslenmis sektor vardir. Bu surum surekli SMART taramasi yapmaz.
- Son analiz sonrasi negatif RAW adimi --dry-run modundadir. Pilot ve otomatik silme icin ayri kullanici onayi olmadan HDD MP4 silinmez.

## Geri alma

RC187 dizinini ve ~/.config/systemd/user/meteormesh-monitor.service dosyasini rollback yedegine alip servisi durdurun/disable edin. Onceki unit'te WorkingDirectory/ExecStart RC186 release'ine dondurulur ve monitor servisi yeniden baslatilir. Analiz, capture, transfer, Control-013 ve retention unit'leri degistirilmez. Retention dry-run kancasini geri almak icin rollback altindaki after_analysis_with_backup.pre-dry-run-gate.py dosyasini analiz sonrasi wrapper'a geri kopyalamak gercek silmeyi etkinlestirir; onay olmadan yapmayin.
