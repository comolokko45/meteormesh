# RC163 — operational telemetry and issue classification

Installed: 2026-11-03 22:39 UTC / 2026-11-04 01:39 TRT

Changed existing files only:
- `~/.local/lib/meteormesh/meteor-heartbeat-watch.py`
- `~/.local/lib/meteormesh/meteor-ops-summary.py`
- `~/.local/lib/meteormesh/meteor-ops-local-alerts.py`

No systemd unit/timer, phone command, purge policy, database, or recording service was changed. The active observer is invoked by the existing `meteormesh-status.service` post-step.

The watcher now records sanitized last-segment-switch, target duration, ready count, and last-completed MP4 time from the phone health endpoint. The current installed 0.13.4 app does not expose completed-MP4 time or ready count in that endpoint; those values remain unknown until the prepared 0.13.5 APK is installed and accepted.

The summary now independently diagnoses network readiness, API, Control-013 heartbeat, segment progress/camera readiness, phone/HDD storage, current-night sync, and current-night analysis. Current-night queue counts come from the latest schema-checked report on the UUID-verified HDD. The 119 historical FAILED jobs remain separate. Daily local digest includes per-device channels, progress, night report, and issue classes after the existing 10:00 TRT trigger. Alert aliases migrate silently; no duplicate raise/recovery is emitted. External delivery stays disabled.

Targeted validation: `py_compile` and self-tests passed for all three scripts. Natural status at 2026-11-03 22:41 UTC read live progress: Ruby and Surya switch timestamps recent; Viva camera readiness false and last switch stale; network READY. HTTP 3/3 online, Control-013 stale 3/3, ADB only Surya, confirmed Node offline 0. Local alert state migrated to `CAPTURE_STALLED:viva` with no duplicate event.

Rollback copies in this directory are `*.pre-rc163`. Restore each to the corresponding active path while `meteormesh-status.service` is inactive; no daemon-reload is needed.
