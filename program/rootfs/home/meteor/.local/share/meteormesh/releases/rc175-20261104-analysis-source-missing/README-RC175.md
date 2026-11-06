# RC175 — source-missing analysis classification

RC160’s guarded source availability check stays intact. This release distinguishes actionable WAITING analysis jobs from WAITING jobs whose exact source MP4 is absent. Missing-source jobs remain unchanged in SQLite and keep their attempt count; the report records `ANALYSIS_SOURCE_MISSING_REVIEW_REQUIRED`, not a false systemd `ANALYSIS_PENDING` failure. No analysis is claimed complete and no media/database rows are changed by this classification.

Targeted verification: Python compile; current production report (99/99 night WAITING jobs source-missing); synthetic cases with one available source and one RUNNING job. All passed.

Production integration is one systemd user drop-in that points `ExecStart`, `WorkingDirectory`, and `METEORMESH_HUB_PATH` at this immutable release. Existing backup/report `ExecStopPost`, timers, queue, database, and capture/pull units remain intact.

Rollback: remove only the RC175 drop-in, run `systemctl --user daemon-reload`, and confirm the prior RC160 `ExecStart` is effective. No release or data file needs deletion.
