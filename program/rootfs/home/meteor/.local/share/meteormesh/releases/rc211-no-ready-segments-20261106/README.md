# RC191 — Negative video retention hardening (2026-11-05)

## Problem found

The RC185 retention CLI treated omission of `--dry-run` as destructive apply and had no per-run delete limit. Candidate adjacency was checked only within the same camera, unreadable successful analysis results were silently ignored, and known neighboring jobs still waiting for analysis did not block deletion. The write-mode SQLite connection also passed a `file://` URI as a normal filename, so the newly needed apply path was not portable to the live Linux runtime.

## Changes

- Dry-run is the default; destructive work requires `--apply` and is limited to 1–5 videos per invocation.
- Candidate and uncertain-analysis time intervals protect all three cameras within ±315 seconds.
- A known neighboring camera job that is not `SUCCEEDED` blocks cleanup until analysis finishes. Each of the three cameras must also have a successful analyzed segment within ±315 seconds; missing camera coverage blocks cleanup.
- Successful jobs without a detector result row, unreadable result JSON, or mismatched hashes are uncertainty and protect all cameras around that time. Unparseable candidate or pending job times fail closed.
- A positive detector result cancels an outstanding `DELETE_PENDING` disposition while the MP4 still exists.
- Interrupted delete recovery completes the tombstone and idempotently repairs the receipt.
- The post-analysis wrapper runs a verified SQLite backup before retention and refuses apply if the backup did not pass. It uses bounded apply only when the existing activation marker explicitly contains `auto_delete_enabled: true`; otherwise it runs dry-run.
- `hub_workflow` reports the same existing policy marker. RC190 phone purge validates that the retention field is boolean but does not treat HDD cleanup state as a phone-purge blocker.
- The existing OpsJournal summary reads the policy and current-night tombstones from SQLite, so the monitor reports live cleanup state instead of a stale hard-coded disabled value.

## Verification

- Python compile: PASS for retention, wrapper, hub workflow, phone-purge, post-analysis gate, and OpsJournal summary.
- Synthetic Linux test suite: 13/13 PASS after coverage hardening. It covers default read-only mode, exact-segment review, cross-camera candidate protection, unreadable/missing result protection, pending-neighbor and incomplete-camera-coverage blocks, single-delete cap, preserved sidecar, SQLite quick check, malformed timestamps, policy gating, and backup-failure gating.
- Existing OpsJournal `--self-test`: PASS on G62 Python after deployment.
- Production acceptance remains separate: dry-run, exact-segment review, one-file pilot, tombstone/receipt verification, then bounded automatic cleanup.

## Deployment and rollback

RC191 is a separate release copied from RC185; RC185 and RC190 source releases remain unchanged. New systemd overrides only redirect future analyzer/hourly-pull executions to RC191 and do not restart an active recording, analyzer, or pull service. Remove the two RC191 drop-ins and run `systemctl --user daemon-reload` to return to the previous paths. Restore the saved `meteor-ops-summary.py` and the pre-RC191 retention policy marker to roll back the status/policy changes. Keep all existing tombstones and receipts during rollback; they are the idempotency record for already completed deletions.

The 32 GiB Surya reserve and 33 GiB resume headroom are unchanged. This retention policy only concerns G62 HDD MP4 files whose analysis and integrity gates pass; it does not delete phone recordings, sidecars, detector results, candidate clips, SQLite data, or meteor review material.
