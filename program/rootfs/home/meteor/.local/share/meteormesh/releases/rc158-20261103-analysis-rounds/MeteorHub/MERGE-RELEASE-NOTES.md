# MeteorMesh unified release candidate RC1 — 30 October 2026

Basis: canonical MM-01 393/393 isolated source package and the previously validated MeteorNode 0.10 HTTP adapter.

Changes only in:
- meteornode_pull.py and meteormesh_hub.py: explicit trusted-LAN HTTP 8080 adapter, pinned HTTPS remains default
- meteormesh_hub.py: optional manual `sync --max-segments 1` with resumable `.part` priority
- meteor_morning.py: optional manual `run --max-segments-per-device 1` to bound end-to-end acceptance; deferred segments yield PARTIAL_REVIEW_REQUIRED, not false success
- hub_workflow.py: optional explicit METEORMESH_DETECTOR_PYTHON and METEORMESH_DETECTOR_CPU_MODE=1 to run HP G62's proven virtual environment/CPU flags

No new SQLite queue, new daemon, auto-delete, Drive upload, LLM executor, or active timer.

Production gate: canonical MM-01 HP staging pass, review, rollback, manual single-video acceptance, and separate permission before systemd timer activation. The installed Hub 0.9 is not modified by this package.

HTTP lab mode is authenticated by an insecure shared test token and lacks transport encryption: trusted isolated LAN only; NEVER publicly port-forward. Production requires a secure transport/credential upgrade.


## 2026-11-02 staging correction — Night-scope acceptance gate correction

- A scoped analysis now requires the configured device IDs to match the armed night scope IDs; a mismatch keeps the run pending and suppresses provisional event output.
- The report records verified segment coverage for every required camera and keeps the run pending if any required camera has no in-scope INGEST_VERIFIED segment.
- Current-night queue completion is evaluated separately from older carryover. Carryover remains visible with an explicit review flag and outcome; exhausted historical failures do not hold a complete current night in ANALYSIS_PENDING.
- Staging RC157 only. The active RC156 release/service and stored media were not modified; no tests were run as part of this correction.

## 2026-11-03 staging update — RC157 delivery scope

- `--only-job-id` is a diagnostic selector for one positive WAITING job and requires `phase=analyze`. When a night scope is supplied, the selected job must pass that exact scope; selection does not fall back to older carryover jobs.
- Scoped analysis acceptance checks that configured camera IDs match the armed night policy and that every required camera has at least one `INGEST_VERIFIED` segment in that night. Older queued work remains visible as a separate carryover review outcome.
- `phase=sync` supports optional per-camera rounds through `METEORMESH_FAIR_SYNC_ROUND_SIZE` (1–100 clips per round); unset or zero keeps the sequential ingest path. This prevents one camera's backlog from consuming all sync work before the others are visited.
- Analysis reclaims stale running jobs but does not automatically requeue exhausted `FAILED` jobs. Historical attempt limits remain intact.
- RC157 is staging only. These changes do not activate timers or modify the RC156 production service; full-night operational acceptance is still pending.


## RC158 analysis rounds — 2026-11-03

- Analysis now reports `ANALYSIS_BATCH_PENDING` and exits successfully only when its configured job budget was reached, eligible scoped work remains, and the batch had no errors. A true error or an exhausted queue remains a distinct outcome.
- The supplied read-only queue guard skips empty scopes, verifies the three-camera config against the armed night policy, and fails closed on missing/unsafe paths.
- The supplied 15-minute user timer runs five-job analysis rounds; the existing `OnSuccess` path performs the lock-guarded verified pull after each completed round. Hourly pull uses RC157 camera round-robin size 10.
- No purge policy, HDD RAW deletion, phone setting, or active capture behavior is enabled by this release.
