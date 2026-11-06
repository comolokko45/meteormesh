import tempfile,time
from pathlib import Path
from unittest.mock import patch
import capture_control as c
with tempfile.TemporaryDirectory() as tmp:
 c.ROOT=Path(tmp)
 d={'id':'cam-ruby'}
 def setup(state):
  return patch.multiple(c.base,read_devices=lambda:[d],read_state=lambda:{'owned':{}},health=lambda _:state,inside_window=lambda:False,process_one=lambda *args:'DAY_IDLE')
 with setup({'state':'finalizing_mp4'}):
  assert c.apply('cam-ruby','stop')['status']=='NEEDS_REARM_OR_STORAGE'
 with setup({'state':'idle'}):
  assert c.apply('cam-ruby','stop')['status']=='VERIFIED'
  assert c.intents()['cam-ruby']['until']>time.time()
 with patch.object(c.base,'read_devices',return_value=[d]),patch.object(c.base,'read_state',return_value={'owned':{}}),patch.object(c.base,'health',side_effect=OSError('offline')):
  try:c.apply('cam-ruby','auto')
  except OSError:pass
  assert 'cam-ruby' not in c.intents()
 with setup({'state':'idle'}):
  assert c.apply('cam-ruby','start')['status']=='NEEDS_REARM_OR_STORAGE'
 print('PASS finalizing is not stopped; idle verified; bounded intent; offline auto reset; idle is not started')
