# MeteorMesh G62 program restore — 6 November 2026

This is a software snapshot of G62 Ubuntu24.04.5, not a disk image. Videos, SQLite rows, scientific data, runtime logs, database backups and Linux virtualenv binaries are excluded. All versioned releases, current helpers, systemd units/drop-ins and relevant staging code are preserved in their original paths. Package versions are recorded under `environment/` inside `program.tar.gz`.

## Download and verify

Clone this backup branch, then enter this directory. `restore.py` reads split files automatically; do not rename or omit parts.

```sh
python3 restore.py --environment-dir ./environment
```

To verify/decrypt everything, supply the **separate recovery key**. It is not in this repository. Install `python3-cryptography` on Ubuntu if absent.

```sh
python3 restore.py --recovery-key /secure/path/RECOVERY_KEY.txt
```

AES-256-GCM protects `private-state.aesgcm` and `android-delivery.aesgcm`. The first holds exact phone credentials, Control-013 identity/certificates, ADB pairing identity and any source file redacted for publication. The second holds MeteorNode0.13.6 source/APK and rollback APK, Mobile1.1.0 source/signed APK, build scripts and project notes. Source access and APK reinstall require that recovery key. The Android ZIP is restored as `/home/meteor/MeteorMesh-Android-Delivery.zip`. Mobile release signing keys remain in the original Windows account; they are not required to reinstall the supplied signed APK.

## Restore onto a clean machine

1. Use Ubuntu24.04 and create user `meteor`. Original absolute paths are intentional. Do not run a restore over an active station: the script refuses different existing files.
2. Install runtime prerequisites: Python3, python3-venv, python3-cryptography, ffmpeg, adb, git and the required native libraries recorded in `environment/apt-manual.txt` / `dpkg-versions.txt`. Virtualenv files are rebuilt rather than copied.
3. Restore software and private configuration:

```sh
sudo python3 restore.py --apply --recovery-key /secure/path/RECOVERY_KEY.txt
sudo python3 -m venv /opt/meteormesh/venv-metdetpy
# Extract environment/pip-freeze.txt from the reassembled program archive first.
sudo /opt/meteormesh/venv-metdetpy/bin/pip install -r environment/pip-freeze.txt
```

4. Reconnect the project HDD at `/srv/meteor-data` using its existing ext4 UUID `444a8a6d-090a-456c-8a9d-d99665c32574`. No formatting command is part of this package. A different disk requires deliberate UUID/configuration changes throughout the storage guards. Video/database contents are not supplied; keep an existing data disk. On a verified but empty project disk, run `python3 initialize_empty_db.py` as `meteor`; it uses the included empty schema and refuses an existing database.
5. Install/sign in to Tailscale on the restored machine. Root-owned Tailscale identity, root mount configuration and the operating system are outside this user-level software export. Restore private Serve HTTPS to loopback8765 after checking the new tailnet DNS identity. The APK address is currently `meteorhub.tail520fb9.ts.net`; update/rebuild Mobile if that identity changes. Do not enable Funnel.
6. Check phone IP addresses and camera readiness. Restored ADB and Control identities help preserve pairing; Android can still require reauthorization/rearming. Existing phone recordings are not included.
7. With HDD, database, network and configuration ready, as user `meteor`:

```sh
systemctl --user daemon-reload
systemctl --user enable --now meteormesh-control-013.service meteormesh-monitor.service
systemctl --user enable --now meteormesh-capture.timer meteormesh-hourly-pull.timer meteormesh-midnight.timer meteormesh-predawn.timer meteormesh-analysis-rounds.timer meteormesh-status.timer
# Keep the separate09:00 job disabled.
systemctl --user disable meteormesh-morning.timer
```

Enable user lingering with `sudo loginctl enable-linger meteor`. Inspect healthz, phone identities, HDD UUID, SQLite and next timer dates before leaving the station unattended. This restore script was validated in an isolated directory, not by reinstalling the active G62.

## Scope and independence

Included MetDetPy checkout commit: see `manifest.json` (`metdet_commit`). Keep its license and attribution. Historical releases are kept because active systemd drop-ins and imports reference multiple release paths. The previous repository's older root `install.sh` is not the installer for this snapshot.

The recovery key is essential for exact configuration/Android recovery. Store an offline copy separately. Losing that key leaves readable G62 source available, but private configuration must be generated and paired again.
