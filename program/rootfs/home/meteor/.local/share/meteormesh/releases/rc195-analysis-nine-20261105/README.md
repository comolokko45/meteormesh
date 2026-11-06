# MeteorMesh RC195 — nine-job analysis batch

Raises the existing scoped analyzer from eight to nine jobs per 15-minute round. The RC194 05:30–18:50 Europe/Istanbul start window, exclusive lock, NightScope, HDD UUID gate, and RC193 dry-run retention hook remain active.

Observed before deployment: 22 seven-job natural rounds averaged 604 seconds; two eight-job rounds completed in 654 and 691 seconds. The first nine-job natural round ran from 17:55:04 to 18:07:21 TRT on 2026-11-05: 9 completed, 0 failed, 737 seconds total, systemd peak 2,031,976,448 bytes RAM, 0 swap. RC193 retention remained dry-run: 5 eligible, 0 deleted. The next timer slot remained available. This accepts the batch change only; sustained nightly capacity is pending.

Install as the lexically final `meteormesh-analyze.service.d` drop-in and run `systemctl --user daemon-reload`. Do not restart the running analyzer. Rollback: remove only this RC195 drop-in and daemon-reload; RC194 then restores the eight-job setting. Neither action changes SQLite or recording files.
