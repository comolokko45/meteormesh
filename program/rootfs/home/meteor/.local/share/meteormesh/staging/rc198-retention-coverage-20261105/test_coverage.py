import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('retention', Path(__file__).with_name('meteor-negative-video-retention.py'))
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)

when = 1_000_000.0
complete = {camera: [when - 300, when, when + 300] for camera in retention.CAMERAS}
assert retention.missing_camera_boundary_coverage(when, complete) == []
missing = {key: list(value) for key, value in complete.items()}
missing['cam-surya'].remove(when)
assert 'cam-surya:current' in retention.missing_camera_boundary_coverage(when, missing)
missing = {key: list(value) for key, value in complete.items()}
missing['cam-viva'].remove(when + 300)
assert 'cam-viva:next' in retention.missing_camera_boundary_coverage(when, missing)
missing = {key: list(value) for key, value in complete.items()}
missing['cam-ruby'][1] += 61
assert 'cam-ruby:current' in retention.missing_camera_boundary_coverage(when, missing)
print('RETENTION_COVERAGE_TARGETED_CHECKS_PASS')
