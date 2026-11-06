# RC185 — no-candidate MP4 retention (production dry-run gate)

Created from the active RC175 MeteorHub source snapshot on 2026-11-04.

## Behavior

- The standalone retention helper supports dry-run and apply modes. The production post-analysis wrapper now always invokes it with --dry-run pending an approved pilot.
- In production, a camera-folder MP4 is not deleted. The guarded apply helper would require a successful empty-results analysis, matching detector/video/result SHA, INGEST_VERIFIED SQLite state, matching sidecar/size/file SHA, and correct HDD UUID.
- Candidate videos and the immediately adjacent same-camera segments are preserved.
- Apply mode records a per-segment SQLite disposition before unlinking. Subsequent RC185 syncs skip pending/deleted dispositions so an intentionally retired MP4 is not fetched again.
- Failed, unfinished, malformed, or unverifiable analysis records are left untouched. Sidecars, detector JSON, event clips, and acceptance media are not deleted.
- Production is currently dry-run-only; no HDD MP4 has been deleted. A limited apply pilot and later ongoing automatic cleanup require separate explicit approvals.

## Rollback

The previous wrapper is kept at ~/.local/share/meteormesh/rollback/20261104-rc185-negative-retention/after_analysis_with_backup.pre-dry-run-gate.py. Restoring it would re-enable apply mode and requires separate user approval. To remove RC185 release-path overrides, preserve the active unit drop-ins first, remove the four RC185 drop-ins from ~/.config/systemd/user/meteormesh-*.service.d/, and run systemctl --user daemon-reload. Leave the additive SQLite source_dispositions table and any receipts in place.
