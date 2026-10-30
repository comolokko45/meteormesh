#!/usr/bin/env bash
# MeteorMesh RC1.2: safe staging of existing user service. NO activation.
set -euo pipefail
umask 077

SRC=/opt/meteormesh/test/rc12-QaxEgP/MeteorMesh-Ortak-RC1.2
UUID=444a8a6d-090a-456c-8a9d-d99665c32574
PY=/opt/meteormesh/venv-metdetpy/bin/python
METDET=/opt/meteormesh/MetDetPy
CFG=$METDET/config/m3det_normal.json
HOME_CFG=$HOME/.config/meteormesh
REL_PARENT=$HOME/.local/share/meteormesh/releases
REL=$REL_PARENT/rc12-20261030
SERVICE_DIR=$HOME/.config/systemd/user/meteormesh-morning.service.d
TIMER_DIR=$HOME/.config/systemd/user/meteormesh-morning.timer.d
SERVICE_OVERRIDE=$SERVICE_DIR/90-meteormesh-rc12.conf
TIMER_OVERRIDE=$TIMER_DIR/90-meteormesh-rc12.conf
DEVICES=$HOME_CFG/devices-viva-lab-rc12.json
TOKEN=$HOME_CFG/tokens.env

fail() { echo "RC12_INSTALL_BLOCKED: $*" >&2; exit 2; }
[ "$(id -u)" != 0 ] || fail 'root kullanma'
[ -f "$SRC/MeteorHub/meteor_morning.py" ] || fail 'RC1.2 staging bulunamadi'
[ -x "$PY" ] || fail 'MetDetPy Python bulunamadi'
[ -f "$CFG" ] || fail 'Manuel testte calisan MetDetPy config bulunamadi'
[ "$(findmnt -rn -M /srv/meteor-data -o TARGET,FSTYPE,UUID)" = "/srv/meteor-data ext4 $UUID" ] || fail 'HDD mount/UUID uyusmuyor'
[ "$(systemctl --user is-active meteormesh-morning.timer || true)" = inactive ] || fail 'Sabah timer beklenmedik aktif'
[ "$(systemctl --user is-enabled meteormesh-morning.timer || true)" = disabled ] || fail 'Sabah timer disabled degil'
[ "$(systemctl --user is-active meteormesh-morning.service || true)" = inactive ] || fail 'Sabah servisi calisiyor'
[ ! -e "$SERVICE_OVERRIDE" ] || fail 'Servis RC12 override zaten var: elle incele'
[ ! -e "$TIMER_OVERRIDE" ] || fail 'Timer RC12 override zaten var: elle incele'

# Pinned source fingerprints; reject accidental wrong-stage deployment.
( cd "$SRC/MeteorHub" && printf '%s\n' \
  '9fc5d809c96629ec910d84ae840384cbf8f6177757de12b7d349776f8b6bb66c  meteor_morning.py' \
  '3666c59614424f0be1eb00cd8fce2497e6561ec13bf918a7fd682b2f5995d21f  meteornode_pull.py' \
  '888b78c8ee757ea26730ccfb5f289d2cb6d9611ba92f550cb9b3118ac7162fe8  mm01_bridge.py' \
  '4e0f2d4c1ecf92e4acadc24b7c9ff429ab08f91075ece173a4e1ea36f4aaea48  hub_workflow.py' \
  'f75e57e7b467ef5a15da106cdb5486cd7ae5c26269d760ddb69c3bf1576ff975  hub_events.py' | sha256sum -c - )

mkdir -p -m 700 "$REL_PARENT" "$HOME_CFG" "$SERVICE_DIR" "$TIMER_DIR"
if [ ! -d "$REL" ]; then
  TMP=$(mktemp -d "$REL_PARENT/.rc12-XXXXXX")
  cp -a "$SRC"/. "$TMP"/
  mv "$TMP" "$REL"
fi
[ -f "$REL/MeteorHub/meteor_morning.py" ] || fail 'Release copy missing'
for f in meteor_morning.py meteornode_pull.py mm01_bridge.py hub_workflow.py hub_events.py; do
  cmp -s "$SRC/MeteorHub/$f" "$REL/MeteorHub/$f" || fail "Release copy mismatch: $f"
done

if [ ! -e "$DEVICES" ]; then
  install -m 600 "$SRC/devices-viva-lab.example.json" "$DEVICES"
fi
[ "$(sha256sum "$DEVICES" | cut -d' ' -f1)" = a6c8aec594c358dacccd60bca6f6ac2d8878d94d9711276dcb09749f5446f791 ] || fail 'Viva config unexpected; not overwriting'

# Never publish/store the real pairing token in code or logs.
# For the current LAN test APK, enter its existing lab token privately.
if [ ! -e "$TOKEN" ]; then
  printf 'Viva laboratuvar tokeni (gizli giris, min 32 karakter): ' >&2
  IFS= read -r -s LAB_TOKEN
  printf '\n' >&2
  [[ "$LAB_TOKEN" =~ ^[A-Za-z0-9_-]{32,256}$ ]] || fail 'Token formati gecersiz'
  printf 'METEOR_NODE_VIVA_TEST_TOKEN=%s\n' "$LAB_TOKEN" > "$TOKEN"
  chmod 600 "$TOKEN"
  unset LAB_TOKEN
fi
[ -f "$TOKEN" ] || fail 'Token environment file missing'
[ "$(stat -c %a "$TOKEN")" = 600 ] || fail 'Token file permissions must be 600'

# Validate syntax and read-only planning before editing user systemd units.
METEORMESH_DETECTOR_PYTHON="$PY" METEORMESH_DETECTOR_CPU_MODE=1 \
  PYTHONDONTWRITEBYTECODE=1 "$PY" "$REL/MeteorHub/meteor_morning.py" plan \
  --config "$DEVICES" --data-dir /srv/meteor-data \
  --metdet-repo "$METDET" --metdet-config "$CFG" > "$REL/plan.json"
"$PY" -B - "$REL/plan.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
assert p['device_ids']==['cam-viva'] and p['config_ready_for_live_run']
assert p['ffprobe_found'] and p['detector_repo_present'] and p['detector_config_present']
print('RC12_READ_ONLY_PLAN_OK')
PY

# Only override the existing user service; never install another service.
cat > "$SERVICE_OVERRIDE" <<EOF
[Service]
WorkingDirectory=$REL/MeteorHub
Environment=METEORMESH_DETECTOR_PYTHON=$PY
Environment=METEORMESH_DETECTOR_CPU_MODE=1
ExecStart=
ExecStart=$PY $REL/MeteorHub/meteor_morning.py run --config $DEVICES --data-dir /srv/meteor-data --metdet-repo $METDET --metdet-config $CFG --max-segments-per-device 1 --max-jobs 1 --max-runtime-hours 2 --detector-timeout-seconds 1200 --min-free-gb 20
EOF
cat > "$TIMER_OVERRIDE" <<'EOF'
[Timer]
Persistent=false
EOF

# Roll back by removing ONLY our named overrides. Never delete originals.
cat > "$REL/rollback-service-override.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[ "$(systemctl --user is-active meteormesh-morning.timer || true)" = inactive ] || { echo 'First disable/stop timer deliberately'; exit 2; }
rm -f "$HOME/.config/systemd/user/meteormesh-morning.service.d/90-meteormesh-rc12.conf" "$HOME/.config/systemd/user/meteormesh-morning.timer.d/90-meteormesh-rc12.conf"
systemctl --user daemon-reload
echo 'RC12_OVERRIDE_REVERTED; source and data retained'
EOF
chmod 700 "$REL/rollback-service-override.sh"
systemctl --user daemon-reload

printf '\n=== SERVIS DURUM ===\n'
systemctl --user show meteormesh-morning.service -p LoadState -p ActiveState -p WorkingDirectory --no-pager
printf '\n=== TIMER DURUM ===\n'
systemctl --user show meteormesh-morning.timer -p LoadState -p ActiveState -p UnitFileState -p NextElapseUSecRealtime --no-pager
printf '\n=== SONUC ===\n'
echo 'RC12_USER_SERVICE_PREPARED; TIMER_DISABLED; NO_VIDEO_TRANSFER; NO_ANALYSIS'
echo "RC12_RELEASE=$REL"
echo 'LAB_ONLY: fixed-key HTTP is NOT production-secure. Three-phone night automation remains disabled.'
