#!/usr/bin/env bash
set -euo pipefail
[ "$(systemctl --user is-active meteormesh-morning.timer || true)" = inactive ] || { echo 'First disable/stop timer deliberately'; exit 2; }
rm -f "$HOME/.config/systemd/user/meteormesh-morning.service.d/90-meteormesh-rc12.conf" "$HOME/.config/systemd/user/meteormesh-morning.timer.d/90-meteormesh-rc12.conf"
systemctl --user daemon-reload
echo 'RC12_OVERRIDE_REVERTED; source and data retained'
