# MeteorMesh RC1 — one release source, no duplicate queue

Origin: canonical MM-01 package `MeteorMesh-MM01-Kanonik-HP-Kabul-2026-10-30.zip` (SHA-256 `7b414b68fa8736b399f6e351a35b1ff4c19d39f5cdcdfbd0c5e84acd6db1230a`).

## Gate A: safe staging, no writes to live HDD (on HP G62)

Extract under `/opt/meteormesh/test/rc1-.../`. Run:

```bash
cd MeteorHub
python3 -B mm01_readonly_preflight.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q test_lab_http_adapter test_mm03_merge
```

Pass indicates a safe staging candidate, NOT production activation. The original canonical MM-01's HP gate is managed by the other MeteorMesh conversation and must pass independently before production changes.

## Gate B: once canonical HP mount acceptance and rollback have been approved

Place the source under a separately versioned `/opt/meteormesh/releases/` directory rather than replacing `/opt/meteormesh/src/MeteorHub-0.9`. The following example is a MANUAL bounded live command that modifies `/srv/meteor-data` only if the operator explicitly decides to run it; it is NOT part of the staging gate.

Create a private config based on `devices-viva-lab.example.json` (no token in config). With Viva on trusted isolated LAN and the temporary laboratory fixed token env variable defined, run:

```bash
cd /opt/meteormesh/releases/YOUR_RC1/MeteorHub
METEORMESH_DETECTOR_PYTHON=/opt/meteormesh/venv-metdetpy/bin/python \
METEORMESH_DETECTOR_CPU_MODE=1 \
python3 meteor_morning.py run \
 --config /opt/meteormesh/config/devices-viva-lab.json \
 --data-dir /srv/meteor-data \
 --metdet-repo /opt/meteormesh/MetDetPy \
 --metdet-config /opt/meteormesh/MetDetPy/config/m3det_normal.json \
 --max-segments-per-device 1 --max-jobs 1 --max-runtime-hours 1 \
 --detector-timeout-seconds 1200 --min-free-gb 2
```

Because the run deliberately defers remaining READY segments, `PARTIAL_REVIEW_REQUIRED` is expected even if one video and one detector job succeed. Check `sync_good`, `jobs_completed_this_run`, and SQLite `SUCCEEDED` values. Do not treat it as a full-night pass. The existing `cam-viva/*.mp4.part` is prioritized by the limited run.

To switch to full 3-phone run after gates, use the same CLI without `--max-segments-per-device` and with a proper 3-device config; no new scheduler or queue is necessary. Activate the existing morning timer **only after explicit approval** and secure transport replacement (HTTP lab token is insecure).

NO phone delete, local delete, Drive transfer, or 3D triangulation is part of RC1.

## RC1.1 FFprobe source-FD fix — extra gate before any new download

The old RC1 manual acceptance returned `FFPROBE_FAILED` after selecting `viva_20261029T152451Z_138f2c357988`. This release fixes the fd inheritance for `ffprobe` and the later MetDetPy child. It does **not** assert the actual MP4 is valid until the read-only diagnostic confirms it.

On HP, from the **newly unpacked RC1.1 directory**, first run:

```
bash quick_check.sh
python3 -B diagnose_ffprobe.py
```

`quick_check.sh` reads live HDD identity and runs only 17 targeted tests in a local source tree (no production write); `diagnose_ffprobe.py` reads the existing DB error and directly probes the selected on-disk MP4, without phone transfer. If direct probe reports `OK`, a further manually approved bounded `meteor_morning.py run --max-segments-per-device 1 --max-jobs 1` can reconcile the existing downloaded MP4 into `INGEST_VERIFIED`, enqueue one job, and analyze. If it fails, inspect the `DB_FFPROBE_DIAGNOSTIC` and `DIRECT_FFPROBE_STDERR` output, do not redownload or delete.

Never run `systemctl enable` or auto purge as part of this release. Keep this code separate from `/opt/meteormesh/src/MeteorHub-0.9` and leave the other conversation's canonical MM-01 staging intact.
