# MeteorNode 0.13.6 timed-muxer beta 1 delivery

Status: build and source contracts PASS; not hardware accepted and not FINAL.

Purpose: separate the normal 300 s encoder-PTS rollover from an emergency-only
file-size ceiling. The previous 0.13.5 candidate could request rollover near
270 s because it used 90% of the nominal 300 s byte estimate.

Contents:

- `source/`: complete buildable source and focused rotation-policy contract.
- `apk/`: versionCode 19 candidate and SHA-256 receipt. APK SHA-256: `e8498913978aad76a3213ca222cea6f606f1a5b8a32d59f8af503c0ba3ccad14`.
- `rollback/`: the verified 0.13.5 candidate APK and checksum.

Required acceptance before three-phone rollout:

1. Install on one idle pilot phone.
2. Produce at least two consecutive segments.
3. Verify first packet keyframe, monotonic per-file PTS, cross-file global PTS,
   decode/ffprobe, SHA-256, sidecar and SQLite ingest.
4. Confirm normal segments reach the 300 s target plus at most keyframe wait.
5. Roll back to 0.13.5 if any boundary or service regression appears.

Build evidence:

- 25/25 source/security/rotation contracts PASS.
- Android API/Build Tools 35.0.0 `:app:assembleDebug` PASS.
- Package `org.meteormesh.node.compactfixedtest`, versionCode 19, targetSdk 35.
- APK Signature Scheme v2 PASS; certificate SHA-256 `55a4b0103a43f9e8382e00e9b04ff8f6ce0d6c3b15426bceeb20c4ca92f518ed`.
