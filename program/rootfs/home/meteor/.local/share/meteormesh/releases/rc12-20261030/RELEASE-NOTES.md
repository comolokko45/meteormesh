# MeteorMesh RC1.1 — FFprobe/MetDetPy pinned FD subprocess repair

30 October 2026. Base: RC1 from canonical MM-01 guarded Hub/Node 0.10 merged sources.

Observed on HP: `FFPROBE_FAILED`, 0 jobs, `PARTIAL_REVIEW_REQUIRED` for `cam-viva`, selected `viva_20261029T152451Z_138f2c357988`. Source video likely published by prior completed, hash-verified transfer; confirm by read-only diagnostic before retry.

Root cause candidate: `/proc/self/fd/<N>/file.mp4` belongs to the parent Python process. Under Python 3 subprocess, all nonexplicit file descriptors are closed; `ffprobe` child cannot reach the intended file through the inherited path. `guarded_detector_run` passed output FD only, not input MP4 FD.

Changed only two production files (one file also receives a retry-order fix):
- `MeteorHub/mm01_bridge.py`: `guarded_ffprobe_run` opens pinned input inode with `O_NOFOLLOW`, inherits only necessary input FD, maintains mount verification. `guarded_detector_run` additionally passes a pinned source MP4 FD along with output FD.
- `MeteorHub/meteormesh_hub.py`: `verify_with_ffprobe` calls guarded helper rather than direct subprocess.

New file `test_rc11_fd_handoff.py`: 3 regression tests (real ffprobe on MP4, child video+output FD handoff, reject symlink input); combined targeted staging smoke 17/17 passes locally. Production MM-01 guard, SQLite schema/queue, MeteorAgent, systemd units, phone auth and detector config unchanged.

Limitations: remote live MP4/DB not accessible to developer. If direct `ffprobe` against `/srv/meteor-data/cam-viva/<id>.mp4` fails, this is not merely FD loss; do not repeat downloader or run detector until recorded stderr investigated. No audio/video deletion and no timer activation. Existing `FFPROBE_FAILED` SQLite state can be safely updated to verified upon successful guarded retry. One-segment run will retain `PARTIAL_REVIEW_REQUIRED` because 9 segments deliberately deferred even when the selected video succeeds.

- Recovery order with a one-segment acceptance limit is now: partial `.mp4.part` first, previously completed local `.mp4` second, fresh downloads last. This prevents an unnecessary ~257MB transfer when the last operation published a file but the probe failed. A focused regression test was added.
