import tempfile,time,uuid
from pathlib import Path
from unittest.mock import patch
import manage_server as m
import capture_base as c
with tempfile.TemporaryDirectory() as tmp:
 m.ROOT=m.capture.ROOT=Path(tmp)
 old={'ActiveState':'inactive','InvocationID':'old','Result':'success'}
 new={'ActiveState':'activating','InvocationID':'new','Result':'success'}
 with patch.object(m,'unit_state',side_effect=[old,new]),patch.object(m.subprocess,'run'):
  row=m.submit({'id':str(uuid.uuid4()),'action':'status','device':'','confirm':True})
  assert row['invocation_id']=='new'
 with patch.object(m,'unit_state',return_value={**old,'InvocationID':'later-timer'}):
  assert m.update_receipts()[0]['status']=='SUPERSEDED_REVIEW_REQUIRED'
 m.write([{**row,'status':'ACCEPTED'}])
 with patch.object(m,'unit_state',return_value={**old,'InvocationID':'new'}):
  assert m.update_receipts()[0]['status']=='SERVICE_COMPLETED'
 calls=[];doc={'owned':{'cam-surya':{}}}
 result=c.process_one({'id':'cam-surya'},doc,True,fetch=lambda d:{'state':'recording:stale','free_bytes':31*1024**3,'remote_camera_ready':False},send=lambda *args:calls.append(args),persist=lambda d:None,await_idle_state=lambda *a,**kw:True)
 assert result=='STOPPED_LOW_STORAGE_RESERVE' and len(calls)==1 and not doc['owned']
 with patch.object(c,'recording_progress_issue',return_value='CAPTURE_STALLED'):
  try:c.process_one({'id':'cam-surya'},{'owned':{'cam-surya':{}}},True,fetch=lambda d:{'state':'recording:stale','free_bytes':40*1024**3})
  except RuntimeError as e:assert 'CAPTURE_STALLED' in str(e)
  else:raise AssertionError('Healthy storage must retain stale-capture check')
 print('PASS invocation correlation, superseded result, bounded dispatch, storage priority, stale capture retained')
