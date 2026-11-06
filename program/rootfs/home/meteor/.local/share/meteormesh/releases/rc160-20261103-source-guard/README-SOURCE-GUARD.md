# RC160 — source-availability guard

Forked from RC159 reboot-resume. Before a WAITING analysis job is claimed, RC160 checks that the exact device MP4 exists as a regular non-symlink file. Missing input stays WAITING, with attempts unchanged, and is reported under `waiting_missing_source`; current-night and carryover counts are separated. Existing SHA-256 and ffprobe checks remain in `run_one_detector`.

No historical FAILED jobs are retried. No media is deleted or modified by this change. RC159 is retained intact for rollback.
