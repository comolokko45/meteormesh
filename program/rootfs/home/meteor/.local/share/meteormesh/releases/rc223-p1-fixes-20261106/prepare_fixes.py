import ast,shutil
from pathlib import Path
root=Path(__file__).parent
p=root/'autopilot_policy.py';s=p.read_text(encoding='utf-8')
s=s.replace("TZ=ZoneInfo('Europe/Istanbul')","TZ=ZoneInfo('Europe/Istanbul')\ndef obj(value):return value if isinstance(value,dict) else {}")
replacements={"rows=phone.get('devices',{})":"rows=obj(phone.get('devices'))","codes=summary.get('assessment',{}).get('codes',[])":"codes=obj(summary.get('assessment')).get('codes',[])\n if not isinstance(codes,list):codes=[]","row=rows.get(name,{})":"row=obj(rows.get(name))","info=row.get('storage',{})":"info=obj(row.get('storage'))","prior=old.get('storage',{}).get(name,{})":"prior=obj(obj(old.get('storage')).get(name))","purge=night.get('phone_purge',{}).get('deleted_by_device',{}).get('cam-'+name)":"purge=obj(obj(night.get('phone_purge')).get('deleted_by_device')).get('cam-'+name)","pull=units.get('meteormesh-hourly-pull.service',{});timer=units.get('meteormesh-hourly-pull.timer',{})":"pull=obj(units.get('meteormesh-hourly-pull.service'));timer=obj(units.get('meteormesh-hourly-pull.timer'))","progress=summary.get('capture_progress',{}).get(name,{})":"progress=obj(obj(summary.get('capture_progress')).get(name))","queue=night.get('detector_summary',{}).get('jobs_in_night',{})":"queue=obj(night.get('detector_summary')).get('jobs_in_night',{})\n if not isinstance(queue,dict):queue={}","elif not window:add('analysis','EXPECTED_WAIT','OUTSIDE_ANALYSIS_WINDOW')\n elif count('FAILED')>0:add('analysis','NEEDS_REVIEW','FAILED_JOBS_REQUIRE_CAUSE_NOT_BLIND_RETRY')":"elif count('FAILED')>0 or 'ANALYSIS_FAILED' in codes:add('analysis','NEEDS_REVIEW','FAILED_JOBS_REQUIRE_CAUSE_NOT_BLIND_RETRY')\n elif not window:add('analysis','EXPECTED_WAIT','OUTSIDE_ANALYSIS_WINDOW')","and night.get('detector_summary',{}).get('analysis_complete') is True":"and obj(night.get('detector_summary')).get('analysis_complete') is True\n      and valid_timer_proof(summary.get('predawn_timer_evidence'),first_night,morning,now))"}
for a,b in replacements.items():
 if a not in s:raise RuntimeError('Missing patch target '+a)
 s=s.replace(a,b)
# Replacement above supplies its own closing parenthesis.
s=s.replace('valid_timer_proof(summary.get(\'predawn_timer_evidence\'),first_night,morning,now))):','valid_timer_proof(summary.get(\'predawn_timer_evidence\'),first_night,morning,now)):')
s=s.replace('def evaluate(summary,previous,now):', '''def valid_timer_proof(value,night,morning,now):
 value=obj(value);trigger=instant(value.get('trigger_utc'));started=instant(value.get('service_started_utc'))
 return (value.get('source')=='SYSTEMD_PROPERTIES' and value.get('night_id')==night
         and value.get('service_result')=='success' and isinstance(value.get('invocation_id'),str)
         and len(value['invocation_id'])==32 and trigger is not None and started is not None
         and abs((trigger-morning).total_seconds())<=120
         and 0<=(started-trigger).total_seconds()<=120 and started<=now)
def evaluate(summary,previous,now):''')
compile(s,str(p),'exec');p.write_text(s,encoding='utf-8')
# Cleanup: propagate errors all the way to systemd, rather than log-and-succeed.
p=root/'after_analysis_with_backup.py';s=p.read_text(encoding='utf-8');s=s.replace('    for batch in range(2):','    failed = False\n    for batch in range(2):').replace('        if backup_status != "OK":','        if backup_status != "OK":\n            failed = True').replace('        print("RC217: batch="','        if status not in ("OK", "DEFERRED"):\n            failed = True\n        print("RC217: batch="').replace('    run_step("REPORT", REPORT, [], 300)\n    return 0','    report_status, _ = run_step("REPORT", REPORT, [], 300)\n    return 1 if failed or report_status != "OK" else 0');p.write_text(s,encoding='utf-8')
p=root/'post-analysis-condition-gate.py';s=p.read_text(encoding='utf-8').replace('rc217-cleanup-capacity-20261106','rc223-p1-fixes-20261106').replace('        return 0\n    try:', '        return 0 if status == "OK" else 1\n    try:').replace('            print("RC199_POST_ANALYSIS: RETENTION_SKIPPED result=" + (result or "unknown") + " BACKUP_ERROR " + type(exc).__name__, flush=True)','            print("RC199_POST_ANALYSIS: RETENTION_SKIPPED result=" + (result or "unknown") + " BACKUP_ERROR " + type(exc).__name__, flush=True)\n            return 1').replace('        print("RC191_POST_ANALYSIS: DELEGATED exit=" + str(child.returncode), flush=True)','        print("RC191_POST_ANALYSIS: DELEGATED exit=" + str(child.returncode), flush=True)\n        return child.returncode').replace('    return 0  # Never mask the analyzer\'s exit result.','    return 1  # Post-processing failure must be observable.');p.write_text(s,encoding='utf-8')
p=root/'cleanup_gate.py';s=p.read_text(encoding='utf-8').replace('rc217-cleanup-capacity-20261106','rc223-p1-fixes-20261106').replace(" subprocess.run(['/usr/bin/python3','-B',str(OLD)],env=env,check=False)\n return 0"," return subprocess.run(['/usr/bin/python3','-B',str(OLD)],env=env,check=False).returncode");p.write_text(s,encoding='utf-8')
# Runtime safety checks must survive python -O.
src=Path('MeteorMesh-GitHub-Preparation-2026-11-06/repository/snapshots/g62-20261106/restore.py')
class Checks(ast.NodeTransformer):
 def visit_Assert(self,node):return ast.copy_location(ast.If(test=ast.UnaryOp(op=ast.Not(),operand=node.test),body=[ast.Raise(exc=ast.Call(func=ast.Name(id='RuntimeError',ctx=ast.Load()),args=[node.msg or ast.Constant('Recovery safety check failed')],keywords=[]),cause=None)],orelse=[]),node)
tree=Checks().visit(ast.parse(src.read_text(encoding='utf-8')));ast.fix_missing_locations(tree);s=ast.unparse(tree)+'\n'
s=s.replace("if not target.resolve().is_relative_to(root):","if target.is_symlink() or target.resolve() != target.absolute() or not target.resolve().is_relative_to(root):")
(root/'restore.py').write_text(s,encoding='utf-8')
