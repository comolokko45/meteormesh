# Backup verification

Snapshot source: live G62 software RC222, 6 November 2026. No videos or runtime database rows included.

- Cloned backup/g62-20261106-rc222 back from GitHub onto G62.
- Reassembled split artifacts and verified every SHA256SUMS entry.
- AES-256-GCM vault and Android archive authenticated/decrypted with the separately held recovery key.
- Restored 1,672 program/configuration files into an isolated temporary root; no production service was started or changed by the restore test.
- Android ZIP: 92 entries; CRC test passed.
- Mobile1.1.0 APK SHA256 matched the original signed build: 7ce82874eedaa1d6fcd0a861732147b3f0cb35e579c8c12c680a79376684ee80.
- Restored Control private key permissions checked: no group/other access.
- Empty SQLite schema loaded successfully in memory; jobs table has zero rows.

This is a tested software-file restore, not a clean operating-system deployment acceptance. Hardware mount/UUID, OS packages, Tailscale login, empty/existing data disk and phone readiness must be completed as described in INSTALL.md. The recovery key and Windows APK signing key are not published.
