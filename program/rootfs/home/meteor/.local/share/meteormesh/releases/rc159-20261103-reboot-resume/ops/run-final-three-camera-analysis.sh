#!/usr/bin/env bash
set -euo pipefail
for unit in meteormesh-analyze.service meteormesh-hourly-pull.service meteormesh-midnight.service meteormesh-morning.service; do
  load=$(systemctl --user show "$unit" -p LoadState --value 2>/dev/null) || { echo "REFUSE: cannot read $unit LoadState" >&2; exit 75; }
  state=$(systemctl --user show "$unit" -p ActiveState --value 2>/dev/null) || { echo "REFUSE: cannot read $unit ActiveState" >&2; exit 75; }
  [ "$load" = loaded ] || { echo "REFUSE: $unit LoadState=$load" >&2; exit 75; }
  case "$state" in inactive|failed) :;; *) echo "REFUSE: $unit ActiveState=$state" >&2; exit 75;; esac
done
if ! mountpoint -q /srv/meteor-data; then
  echo 'REFUSE: /srv/meteor-data is not mounted' >&2
  exit 73
fi
free_bytes=$(df -B1 --output=avail /srv/meteor-data | tail -n 1 | tr -d ' ')
if [ "$free_bytes" -lt 107374182400 ]; then
  echo 'REFUSE: HDD is below the 100 GiB safety reserve' >&2
  exit 74
fi
/usr/bin/python3 -B /home/meteor/.local/share/meteormesh/staging/rc157-20261102-fair-sync/ops/check-night-inventory.py
exec /usr/bin/python3 -B /home/meteor/.local/share/meteormesh/staging/rc157-20261102-fair-sync/MeteorHub/meteor_morning.py run \
  --config /home/meteor/.config/meteormesh/devices-three-lab-rc12.json \
  --night-scope /home/meteor/.local/share/meteormesh/staging/rc157-20261102-fair-sync/ops/night-scope-20261101-final.json \
  --data-dir /srv/meteor-data --metdet-repo /opt/meteormesh/MetDetPy \
  --metdet-config /opt/meteormesh/MetDetPy/config/m3det_normal.json \
  --max-jobs 500 --max-runtime-hours 16 --detector-timeout-seconds 1800 \
  --min-free-gb 100 --phase analyze
