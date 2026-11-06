import importlib.util
from datetime import datetime, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location('monitor', Path(__file__).with_name('meteor-monitor-server.py'))
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
timers = [{'unit': 'meteormesh-morning.timer', 'last_local': '2026-11-05T09:00:00+03:00'},
          {'unit': 'meteormesh-predawn.timer', 'last_local': '2026-11-05T05:30:00+03:00'}]
services = {'morning': {'state': 'inactive', 'result': 'success'},
            'predawn': {'state': 'inactive', 'result': 'success'}}
report = {'today_sync_reports': [{'night_id': '20261105-night', 'phase': 'sync',
          'finished_local': '2026-11-05T19:02:00+03:00', 'outcome': 'INGEST_QUEUED'}]}
result = monitor.natural_timer_gates(timers, services, {}, report, '20261105-night',
                                     datetime.fromisoformat('2026-11-05T19:20:00+03:00'))
assert result['morning']['status'] == 'PARTIAL'
assert 'henüz gelmedi' in result['morning']['evidence']
assert '2026-11-06T09:00:00+03:00' in result['morning']['evidence']
timers[0]['last_local'] = '2026-11-06T09:00:00+03:00'
report['today_sync_reports'][0]['finished_local'] = '2026-11-06T09:07:00+03:00'
result = monitor.natural_timer_gates(timers, services, {}, report, '20261105-night',
                                     datetime.fromisoformat('2026-11-06T09:15:00+03:00'))
assert result['morning']['status'] == 'PARTIAL'
assert 'aidiyeti ayrıca doğrulanmalı' in result['morning']['evidence']
assert '09:07' in result['morning']['evidence']
print('MORNING_GATE_TARGETED_CHECKS_PASS')
