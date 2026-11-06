RC212 final policy corrections, 2026-11-06.
The 09:00 timer is optional and remains disabled. Daily analysis starts at 05:30, then existing 15-minute queue rounds continue.
Heartbeat/Agent, OpsJournal summary, monitor timer labels, 05:30 queue gates, and SQLite deletion receipt display were corrected.
Rollback: remove 80-rc212-final-policy.conf and 90-rc212-final-gates.conf; restore rollback/meteor-ops-summary.py.pre-rc212 and rollback/meteor-heartbeat-watch.py.pre-rc212 to ~/.local/lib/meteormesh; run systemctl --user daemon-reload; restart meteormesh-monitor.service. Do not change or stop analyzer/capture units.
