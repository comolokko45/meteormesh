# MeteorMesh RC193 — Bounded dry-run

## Root cause

When negative-video auto-delete is disabled, the existing analyzer post-hook runs a full read-only dry-run after every 7-job batch. On G62, a natural run held the analyzer/pull lock for about six minutes and caused two 5-minute pull ticks to skip. The active deletion policy was also inconsistent with the user's latest statement that no real-delete or continuous-delete approval had been granted.

## Change

- The live `auto_delete_enabled` marker is set to `false`; the original JSON was preserved beside it with a timestamped `.pre-pause-*` rollback copy.
- The wrapper still requires a verified SQLite backup before any retention action.
- With policy disabled, the wrapper runs dry-run only and supplies `--max-eligible 5`.
- The retention script stops after hashing/reporting five eligible MP4s in dry-run mode. Apply-mode deletion cap and safety gates are unchanged.
- No analyzer, capture, phone, database, raw MP4, or timer was stopped or modified by this package.

## Verification

- Targeted unit tests: 15/15 PASS, including five-candidate dry-run limit, no mutation in dry-run, explicit-policy routing, candidate protection, and existing apply caps.
- The production analyzer was left running; this release applies to its next post-analysis hook after deployment.
- Live acceptance must show policy disabled, no new receipts, a bounded dry-run result, and automatic pull after the lock clears.

## Rollback

Remove only the RC193 analyzer-service drop-in, run `systemctl --user daemon-reload`, and the previous RC191 post-analysis gate becomes active again. Keep the negative-retention activation marker `false` until the user explicitly authorizes a real pilot or continuous deletion. Do not restore the `.pre-pause-*` marker as part of code rollback.
