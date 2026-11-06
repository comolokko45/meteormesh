#!/usr/bin/python3
"""Build the current night policy in a separate file for hourly sync.
Does not alter the scope used by the active analyze/midnight services."""
import datetime as dt
import importlib.util
from pathlib import Path

stage = Path.home() / '.local/share/meteormesh/staging/fullnight'
source = stage / 'meteor-auto-night-policy.py'
output = stage / 'night-scope-hourly-current.json'
spec = importlib.util.spec_from_file_location('meteor_auto_night_policy', source)
if spec is None or spec.loader is None:
    raise SystemExit('POLICY_HELPER_UNAVAILABLE')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
original, fingerprint, baseline = policy.read_baseline()
doc = policy.build(dt.datetime.now(dt.timezone.utc), original, fingerprint, baseline)
policy.assert_scope_valid(policy.ensure_paths(), doc)
state = policy.atomic_write(output, doc)
print(f'HOURLY_SCOPE {state} {doc["night_id"]} {doc["start_utc_ms"]} {doc["end_utc_ms"]}')
