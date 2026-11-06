# RC177 timed-muxer acceptance tool

Read-only verifier for two or more consecutive 0.13.6 duration-rotated segments.
It checks APK identity from the sidecar, MP4 SHA/size, ffprobe decode metadata,
first-packet keyframe, strict packet PTS, target duration and the global PTS/UTC
boundary between files. It never writes media, SQLite or phone storage.

`boundary_verified_by_g62=true` is emitted only when every selected file and
boundary passes. A phone sidecar deliberately remains unverified until this
external acceptance succeeds.
