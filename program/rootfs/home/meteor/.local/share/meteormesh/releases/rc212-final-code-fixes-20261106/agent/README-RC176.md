# RC176 — NightScope-aware central status

Purpose: prevent historical detector failures from being reported as current-night
failures while preserving fail-closed behavior when a trusted current NightScope
report is unavailable.

Behavior:

- current-night FAILED, source-missing WAITING and historical FAILED are separate;
- latest sync report can inherit source-missing count from a recent analysis report
  only when both have the same night ID;
- the report night ID must match the expected Europe/Istanbul observing night;
- a stale/missing/untrusted scope falls back to the global DB warning;
- no retry, enqueue, deletion, service restart or database write is added;
- `PARTIAL_NIGHT` remains a warning.

Rollback: remove the RC176 systemd ExecStart drop-in and daemon-reload. Existing
collector, Agent release, database and media remain untouched.
