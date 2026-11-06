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
