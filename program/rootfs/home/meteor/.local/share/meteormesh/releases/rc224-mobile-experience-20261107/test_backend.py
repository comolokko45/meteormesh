import tempfile,uuid
from pathlib import Path
from unittest.mock import patch
import manage_server as m
import capture_control as c
with tempfile.TemporaryDirectory() as tmp:
 m.ROOT=c.ROOT=Path(tmp)
 def doc(action='camera_start',duration=15):return {'id':str(uuid.uuid4()),'action':action,'device':'cam-ruby' if action.startswith('camera_') else '','duration_minutes':duration,'confirm':True}
 for value in (True,0,1,90,'15',None):
  try:m.submit(doc(duration=value))
  except ValueError as e:assert str(e)=='DURATION_NOT_ALLOWED'
  else:raise AssertionError('Invalid duration accepted')
 request=doc()
 with patch.object(m.threading,'Thread') as thread:
  r=m.submit(request);assert r['duration_minutes']==15
  assert thread.call_args.kwargs['args'][-1]==15
 try:m.submit({**request,'duration_minutes':30})
 except ValueError as e:assert str(e)=='REQUEST_ID_CONFLICT'
 else:raise AssertionError('Reused ID with different intent accepted')
 m.write([])
 with patch.object(m.subprocess,'run') as dispatch,patch.object(m,'unit_state',return_value={'ActiveState':'inactive'}):
  r=m.submit(doc('analysis_timer_pause',60));assert r['status']=='TIMER_STATE_VERIFIED'
  args=dispatch.call_args.args[0];assert args==['systemctl','--user','stop','meteormesh-analysis-rounds.timer']
 with patch.object(c.base,'read_devices',return_value=[{'id':'cam-ruby'}]),patch.object(c.base,'read_state',return_value={'owned':{}}),patch.object(c.base,'health',return_value={'state':'recording'}),patch.object(c.base,'inside_window',return_value=False),patch.object(c.base,'process_one',return_value='RECORDING_OWNED'),patch.object(c.time,'time',return_value=1000):
  c.apply('cam-ruby','start',15)
  intent=c.intents()
  assert intent['cam-ruby']['until']==1900
  assert c.desired('cam-ruby',False,intent,1899) is True
  assert c.desired('cam-ruby',False,intent,1900) is False
  c.apply('cam-ruby','auto')
  assert 'cam-ruby' not in c.intents()
 print('PASS duration schema, idempotence, bounded camera dispatch, fixed timer allowlist')
