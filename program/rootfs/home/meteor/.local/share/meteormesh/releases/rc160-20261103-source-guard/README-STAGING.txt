MeteorMesh RC158 operational candidate — 2026-11-03
Baseline: RC157 fair-sync candidate. Final night acceptance remains open.

Changes
- Analyze at most five scoped jobs per run; successful batch yield leaves
  eligible work for a later round. Terminal incomplete coverage becomes
  ANALYSIS_COMPLETE_REVIEW_REQUIRED rather than a false pending retry loop.
- Read-only queue guard requires mounted HDD, exact three-camera config, current
  scope, and eligible WAITING/RUNNING jobs.
- Analysis timer runs at minute 10/25/40/55. OnSuccess->hourly-pull is cleared;
  OnFailure recovery remains. The natural hourly timer is the normal sync source.
- Hourly verified pull uses per-camera rounds of ten segments and retains the
  existing active-unit/flock guards.

Validation
- Batch-yield, terminal-review, and camera-fairness targeted tests passed;
  modified Python files passed py_compile.
- Calendar syntax accepted by systemd-analyze; manifest entries checked.

Live status at 14:26 TRT
- 14:00 race auto-recovered through the old OnSuccess path; pull at 14:00:40
  completed before 14:09. 14:10 empty-queue skip also launched one final pull
  under the old path; it completed before 14:19. Both reports are
  PARTIAL_REVIEW_REQUIRED: no Surya READY segments, 127 scoped jobs terminal,
  zero new jobs. No phone or HDD RAW files were deleted.
- OnSuccess is now empty; OnFailure remains. The 14:25 analyzer tick skipped
  ANALYZE_QUEUE_EMPTY and did not start an hourly pull. Analyzer is inactive;
  next tick is 14:40 TRT. The normal hourly timer is next due 15:00 TRT.
- Automatic phone purge and Drive upload remain disabled. Surya last measured
  35.19 GiB free, only 3.19 GiB above the 32 GiB reserve. Surya coverage, safe
  storage cycling, and the 300-second rollover gap keep the natural 19:00–05:00
  three-camera acceptance open.

Rollback
- Disable meteormesh-analysis-rounds.timer, remove only the two RC158 service
  drop-ins, then run systemctl --user daemon-reload. Do not stop an active
  verified pull; it keeps the command with which it started.
