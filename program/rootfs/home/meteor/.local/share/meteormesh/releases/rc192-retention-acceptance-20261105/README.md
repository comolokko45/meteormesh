# MeteorMesh Monitor RC192 — Retention Acceptance

RC192 keeps the RC188d monitor, API contract, 12 sections, localhost-only listener, and read-only behavior. It corrects the negative-video cleanup status using the live RC191 policy marker and existing OpsJournal disposition evidence.

The monitor now distinguishes:

- **Software / bounded pilot:** PASS only when the live policy is enabled, the per-hook cap is 1–5, and at least one `NO_CANDIDATE_DELETED` receipt exists. The receipt total includes the previously confirmed one-file pilot and later natural deletions.
- **Operational natural run:** PARTIAL until a deletion receipt timestamp is at or after the policy enable timestamp; then PASS. Disabled or unreadable policy is BLOCKED.

The cleanup page shows the policy state, activation time, cap, pilot/software gate, post-activation natural-run gate, and last deletion receipt. It remains read-only and exposes no delete action. Policy read failure is fail-closed for the displayed status. The old night-scope `retention_auto_delete` field is a stored snapshot and is not used as live policy truth.

No capture, transfer, analyzer, phone, database, timer, Tailscale, or cleanup service is changed by this monitor package. The monitor reads its existing RC180 OpsJournal preview plus the live policy marker.

Validation: focused `test_analysis_execution.py` suite; Python compile; inline JavaScript syntax check. Deployment is a separate versioned systemd user-service override with the previous RC188d target retained for rollback.
