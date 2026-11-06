"""One-time evidence-based backfill; fail closed on ambiguous journal history."""
import datetime as dt,importlib.util,subprocess
from pathlib import Path
src=Path(__file__).with_name('meteor-ops-local-alerts.py')
spec=importlib.util.spec_from_file_location('ops',src);ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)
state=ops.load_state()
assert 'STORAGE_LOW:surya' in state['active']
assert 'STORAGE_LOW:surya' not in state.get('opened_at',{})
r=subprocess.run(['journalctl','--user','-u','meteormesh-status.service','--no-pager','--output=short-iso'],capture_output=True,text=True,timeout=15,check=True)
lines=[s for s in r.stdout.splitlines() if 'METEORMESH_OPS_ALERT: STORAGE_LOW:surya' in s or 'METEORMESH_OPS_RECOVERED: STORAGE_LOW:surya' in s]
assert len(lines)==1 and 'OPS_ALERT' in lines[0], 'Ambiguous incident history'
stamp=lines[0].split(' ',1)[0]
parsed=dt.datetime.fromisoformat(stamp)
assert parsed.tzinfo is not None and parsed<=dt.datetime.now(dt.timezone.utc)
state.setdefault('opened_at',{})['STORAGE_LOW:surya']=parsed.astimezone(dt.timezone.utc).isoformat(timespec='seconds')
ops.persist(state)
print('SURYA_INCIDENT_OPENED_FROM_JOURNAL:',state['opened_at']['STORAGE_LOW:surya'])
