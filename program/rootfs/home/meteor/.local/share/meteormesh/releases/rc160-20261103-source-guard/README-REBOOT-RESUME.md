MeteorMesh RC159 — Reboot Resume
=================================

Derived from RC158 analysis-rounds. This is a staged release for unattended
restart recovery; the live systemd user services are switched only after the
existing run is idle.

Changes:
- Reclaim only interrupted RUNNING analysis rows older than 60 seconds, while
  holding the existing .meteor-morning.lock and applying the existing NightScope.
- Before publishing each new detector result, durably publish a hidden recovery
  receipt with the job identity and result SHA-256. A later scoped run reconciles
  only an exact receipt/result match; otherwise it fails closed and never
  overwrites an existing MDRF result.
- SQLite remains transactional with the existing DELETE journal; no history-wide
  FAILED retry, NightScope bypass, purge, or HDD deletion was added.

G62 boot readiness:
- User manager linger is enabled; the systemd timers are enabled and persistent.
- The 00:30 timer is now Persistent=true.
- Phone/capture/pull/analysis/nightly services use a bounded networkd, route, and
  Tailscale readiness gate before running.
- Existing MM-01 mount, exclusive flock, queue guards, and timer cadence remain.

Actual reboot was not performed while the night capture is in service.
