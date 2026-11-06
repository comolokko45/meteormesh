# MeteorMesh RC194 — Analysis capacity and capture-window guard

Date: 2026-11-05

## Change

- Raises the bounded analysis round from 7 to 8 sequential jobs.
- Allows new analysis rounds only from 05:30 through 18:49 Europe/Istanbul.
- Keeps `Nice=10`, the existing exclusive lock, NightScope, detector timeout, disk reserve and RC193 post-analysis hook unchanged.
- Does not stop an active analysis round and does not restart the analyzer.

The measured seven-job rounds used roughly 9–10 minutes of the 15-minute interval. Eight sequential jobs provide a nominal 32 jobs/hour while leaving the 18:55 round blocked before the 19:00 capture window.

## Deployment

The release is copied to:

`/home/meteor/.local/share/meteormesh/releases/rc194-analysis-capacity-20261105`

The drop-in is installed after the active RC193 drop-in. A user daemon reload is sufficient; no running service is restarted.

## Rollback

Remove only the RC194 analyzer drop-in, then run:

`systemctl --user daemon-reload`

The previous 7-job RC191 command and RC193 post-hook become effective again.
