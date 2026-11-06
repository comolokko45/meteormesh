# MeteorNode 0.13.6 — timed muxer beta 1

- Package: `org.meteormesh.node.compactfixedtest`; `versionCode=19`; `versionName=0.13.6-pin0000-timed-muxer-beta1`.
- Based on the local 0.13.5 timed-muxer candidate and built as a separate candidate tree.
- Capture uses one Camera2 session and a surface-input H.264 `MediaCodec`. Normal `MediaMuxer` rollover is driven by the configured encoder-PTS duration and aligned to a sync frame. The camera session and encoder remain active through rollover.
- The former 90%-of-nominal size trigger was removed. A separate emergency ceiling at 125% of nominal segment bytes bounds material encoder overshoot without cutting a nominal 300 s segment near 270 s.
- The 32 GiB protected phone reserve and next-file headroom now reserve against the larger emergency ceiling. No automatic deletion was added to the phone app.
- Sidecars store global encoder PTS start/end and encoded sample count. `segment_boundary_verified` deliberately remains `false` until a phone run and cross-file PTS/ffprobe review pass.
- `:app:assembleDebug` succeeded with API/Build Tools 35.0.0. Artifact SHA-256 is `e8498913978aad76a3213ca222cea6f606f1a5b8a32d59f8af503c0ba3ccad14`; signer certificate SHA-256 is `55a4b0103a43f9e8382e00e9b04ff8f6ce0d6c3b15426bceeb20c4ca92f518ed`, matching the 0.13.5 update certificate.
- Not installed or hardware-tested. Pilot on one idle phone first; do not roll out to all devices until cross-file PTS/ffprobe acceptance passes.

Android API note: `MediaRecorder.setMaxDuration()` stops the recorder when the limit is reached; `setNextOutputFile()` switches only after a file-size boundary. The candidate therefore does not use either API for timed rollover. See https://developer.android.com/reference/android/media/MediaRecorder.
