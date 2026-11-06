# MeteorMesh Mobile 1.1.0

Bu sürüm G62 Bilgi Monitörü'nü ve yetkili uzaktan yönetim ekranını açar. Tailscale Serve üzerinde doğrulanmış adres `https://meteorhub.tail520fb9.ts.net/` etkin ve özel tailnet'e açıktır. APK kaynak kodunda yönetim anahtarı yoktur; uygulama aynı imza ile 1.0.1 üzerine güncellenir.

Kurulum: `dist/MeteorMesh-Mobile-1.1.0.apk` dosyasını F7 Ultra'ya kopyalayıp yükle. Tailscale açıkken uygulamayı aç. Üstteki **Yönet** düğmesine bas ve `%LOCALAPPDATA%\MeteorMeshMobile\management\YONETIM_ANAHTARI.txt` içeriğini gir. Anahtarı APK ile birlikte paylaşma.

Desteklenen işlemler: durum yenileme, mevcut saatlik aktarım ve analiz servislerine talep, otomatik kayıt kontrolü, Ruby/Surya/Viva için bir saatlik kayıt başlatma/mola ve otomatiğe dönüş. Her komut öncesi ekrandaki onay istenir. Sonuç kaydı, gerçek veri işinin tamamlandığını tek başına kanıtlamaz; monitördeki analiz/aktarımı ayrıca izle.

Ayrıntı ve rollback: `../METEORMESH_RC218_UZAKTAN_YONETIM_2026-11-06.md`.

Derleme: mevcut JDK17, Android SDK35, aynı imza anahtarı ve `Build-Release.ps1`. Anahtar `%LOCALAPPDATA%\MeteorMeshMobile\signing` dizininde saklanır ve bu kaynak koduna eklenmez.