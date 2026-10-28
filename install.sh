#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR=/opt/meteormesh
DATA_DIR=/srv/meteor-data
EXPECTED_HDD_UUID=444a8a6d-090a-456c-8a9d-d99665c32574
HUB_VERSION=MeteorHub-0.9
HUB_URL=https://raw.githubusercontent.com/comolokko45/meteormesh/cbf3a149cba990383be035913662d366e9d6d5a1/releases/MeteorHub-0.9-source.zip
HUB_SHA256=0d9d4359a3b71d14a31cb754790b3131462fd272921ae14290f4cbaf32a900a1

if [[ $(id -u) -eq 0 ]]; then
  echo 'HATA: Komutu root yerine meteor SSH kullanıcısıyla çalıştır.' >&2
  exit 1
fi

for tool in findmnt sudo sha256sum mktemp; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "HATA: $tool komutu kurulu değil." >&2
    exit 1
  fi
done

# Do not create a data directory on the SSD if the HDD is unplugged.
mounted_uuid=$(findmnt -nr -o UUID --mountpoint "$DATA_DIR" || true)
mounted_type=$(findmnt -nr -o FSTYPE --mountpoint "$DATA_DIR" || true)
if [[ "$mounted_uuid" != "$EXPECTED_HDD_UUID" || "$mounted_type" != ext4 ]]; then
  echo "HATA: HDD, $DATA_DIR noktasında beklenen UUID ile ext4 olarak bağlı değil." >&2
  echo "Bulunan UUID: ${mounted_uuid:-yok}; dosya sistemi: ${mounted_type:-yok}" >&2
  exit 1
fi

echo '[1/5] Ubuntu için gereken araçlar kuruluyor...'
sudo apt-get update
sudo apt-get install -y ca-certificates curl unzip git ffmpeg python3 python3-venv

echo '[2/5] SSD yazılım klasörleri hazırlanıyor...'
run_user=$(id -un)
run_group=$(id -gn)
sudo install -d -o "$run_user" -g "$run_group" "$APP_DIR" "$APP_DIR/src" "$APP_DIR/config"

echo '[3/5] MeteorHub 0.9 GitHub üzerinden indiriliyor...'
archive=$(mktemp --tmpdir meteorhub-0.9.XXXXXXXX.zip)
trap 'rm -f -- "$archive"' EXIT
curl --fail --location --silent --show-error --retry 3 --retry-delay 2 "$HUB_URL" --output "$archive"
printf '%s  %s\n' "$HUB_SHA256" "$archive" | sha256sum --check --status || {
  echo 'HATA: ZIP SHA-256 doğrulaması başarısız; kurulum durduruldu.' >&2
  exit 1
}
unzip -n -q "$archive" -d "$APP_DIR/src"
test -f "$APP_DIR/src/$HUB_VERSION/meteormesh_pipeline.py"

echo '[4/5] HDD video klasörleri ve SQLite veritabanı hazırlanıyor...'
# The mountpoint has been verified; do not recursively change existing content.
sudo chown "$run_user:$run_group" "$DATA_DIR"
mkdir -p "$DATA_DIR/cam-ruby" "$DATA_DIR/cam-viva" "$DATA_DIR/cam-surya" "$DATA_DIR/morning-reports" "$DATA_DIR/event-indexes"
python3 "$APP_DIR/src/$HUB_VERSION/meteormesh_pipeline.py" --data-dir "$DATA_DIR" init

echo '[5/5] Kaynak testleri ve bağlama noktası kontrol ediliyor...'
(cd "$APP_DIR/src/$HUB_VERSION" && python3 -m unittest discover -q)
findmnt --mountpoint "$DATA_DIR" --output TARGET,SOURCE,FSTYPE
df -hT "$DATA_DIR"
echo "METEORMESH 0.9 HAZIR: yazılım=$APP_DIR/src/$HUB_VERSION, video/veri=$DATA_DIR"
echo 'Not: Gerçek telefon eşleştirmesi, MetDetPy kurulumu ve zamanlayıcı ayrıca yapılacak.'
