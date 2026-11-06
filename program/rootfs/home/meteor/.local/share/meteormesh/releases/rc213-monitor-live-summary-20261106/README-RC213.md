RC213 monitor summary-source correction, 2026-11-06.
Cause: RC212 monitor still read the legacy RC180 OpsSummary, so the intentionally disabled 09:00 timer remained an ERROR.
Change: use the active patched ~/.local/lib/meteormesh/meteor-ops-summary.py in read-only --preview mode.
Rollback: remove 91-rc213-live-summary-source.conf, run systemctl --user daemon-reload, restart meteormesh-monitor.service. RC212 remains the previous monitor release. This release also contains rollback/meteor-monitor-server.py.pre-rc213.
No analyzer, capture, timer, database, or video operation was changed.
