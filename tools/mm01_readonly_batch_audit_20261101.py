import ast, hashlib, json, pathlib, subprocess, sys

home = pathlib.Path.home()
base = home / '.local/share/meteormesh/releases'
versions = ['rc15-20261031-consolidated', 'rc152-20261101-bounded-retry']
critical = ['meteor_morning.py','meteornode_pull.py','meteormesh_pipeline.py','meteormesh_hub.py','hub_workflow.py','boundary_review.py','event_package.py','meteor_review.py','meteor_lab.py','meteor_agent_lite.py']
entry = set(critical) - {'hub_workflow.py'}
redflags = []
print('MM01_BULK_READONLY_AUDIT v1; WRITES=0')
for v in versions:
    d = base / v / 'MeteorHub'
    print('\n===', v, '===')
    if not d.is_dir():
        print('RELEASE_MISSING');redflags.append(v+':RELEASE_MISSING');continue
    pf = subprocess.run([sys.executable, '-B', str(d/'mm01_readonly_preflight.py')],capture_output=True,text=True,timeout=15)
    try:
        j=json.loads(pf.stdout)
        good=(pf.returncode==0 and j.get('status')=='VERIFIED_READ_ONLY_PREFLIGHT' and j.get('writes_performed')==0 and j.get('uuid')=='444a8a6d-090a-456c-8a9d-d99665c32574' and j.get('fstype')=='ext4')
    except (ValueError,TypeError):good=False
    print('PREFLIGHT', 'PASS' if good else 'FAIL', 'exit',pf.returncode)
    if not good:redflags.append(v+':PREFLIGHT_FAIL')
    for f in ['storage_guard.py','mm01_bridge.py',*critical]:
        p=d/f
        if not p.is_file():
            print('MISSING',f);redflags.append(v+':MISSING:'+f);continue
        s=p.read_text(encoding='utf-8')
        try:ast.parse(s, filename=str(p))
        except SyntaxError as exc:
            print('SYNTAX_FAIL',f,exc.lineno);redflags.append(v+':SYNTAX:'+f);continue
        if f=='storage_guard.py':
            checks=['MOUNT_NOT_VERIFIED','UUID_DEVICE_MISMATCH','MOUNT_READ_ONLY','O_NOFOLLOW','O_EXCL','KernelEvidenceSource']
            missing=[c for c in checks if c not in s]
            print('GUARD_SHA256',hashlib.sha256(p.read_bytes()).hexdigest()[:16],'REQUIRED_MARKERS', 'PASS' if not missing else 'MISSING:'+','.join(missing))
            if missing:redflags.append(v+':GUARD_MARKERS')
            continue
        if f=='mm01_bridge.py':
            checks=['guarded_cli','guarded_db_connect','verify_write_path','guarded_partial_open','guarded_publish']
            missing=[c for c in checks if c not in s]
            print('BRIDGE_SHA256',hashlib.sha256(p.read_bytes()).hexdigest()[:16],'REQUIRED_MARKERS', 'PASS' if not missing else 'MISSING:'+','.join(missing))
            if missing:redflags.append(v+':BRIDGE_MARKERS')
            continue
        calls = sum(s.count(n+'(') for n in ['_mm01_verify','_mm01_db','_mm01_open_partial','_mm01_output_dir','_mm01_detector_run','_mm01_publish','_mm01_ffprobe_run','_mm01_guarded_cli','_mm01_process_output'])
        cli = '_mm01_guarded_cli(' in s if f in entry else None
        print('MODULE',f,'gate_calls',calls,'CLI_GUARD', 'YES' if cli else ('NO' if cli is False else 'N/A'))
        if calls==0 or cli is False:redflags.append(v+':NO_GUARD_CALL:'+f)
    code = r'''
import pathlib,sys
sys.path.insert(0,sys.argv[1])
from storage_guard import GuardedStorage,StoragePolicy,StorageDenied
from mm01_bridge import verify_write_path,guarded_db_connect
cases={
 'BAD_UUID':lambda: GuardedStorage(StoragePolicy(uuid='00000000-0000-0000-0000-000000000000')).__enter__(),
 'NO_MOUNT':lambda: GuardedStorage(StoragePolicy(root='/srv/mm01-nonexistent-audit-only')).__enter__(),
 'DIRECT_PRODUCTION_WRITE':lambda: verify_write_path(pathlib.Path('/srv/meteor-data/mm01-audit-never-create')),
 'DIRECT_PRODUCTION_DB':lambda: guarded_db_connect(pathlib.Path('/srv/meteor-data/meteorhub.sqlite3')),
}
failed=[]
for key,call in cases.items():
 try:call()
 except StorageDenied as ex: print(key,'DENIED',str(ex))
 except Exception as ex: print(key,'UNEXPECTED_EXCEPTION',type(ex).__name__,str(ex));failed.append(key)
 else: print(key,'UNEXPECTEDLY_ALLOWED');failed.append(key)
raise SystemExit(bool(failed))
'''
    neg=subprocess.run([sys.executable,'-B','-c',code,str(d)],capture_output=True,text=True,timeout=15)
    print('NEGATIVE_TESTS', 'PASS' if neg.returncode==0 and neg.stdout.count(' DENIED ')==4 else 'FAIL')
    print(neg.stdout.strip() or neg.stderr.strip()[:500])
    if neg.returncode or neg.stdout.count(' DENIED ')!=4:redflags.append(v+':NEGATIVE_TESTS_FAIL')
print('\n=== ACTIVE_SYSTEMD_REFERENCES ===')
for unit in ('meteormesh-morning.service','meteormesh-analyze.service','meteormesh-capture.service'):
    p=subprocess.run(['systemctl','--user','show',unit,'-p','ExecStart','--no-pager'],capture_output=True,text=True,timeout=8)
    line=p.stdout.strip()
    found=next((v for v in versions if v in line),'OTHER_PATH')
    print(unit,found,'OK' if p.returncode==0 else 'UNAVAILABLE')
print('=== RESULT ===', 'STATIC_AND_NEGATIVE_PASS__MANUAL_WRITE_PATH_REVIEW_STILL_REQUIRED' if not redflags else 'NEEDS_REVIEW', ';'.join(redflags) if redflags else 'no_failed_checks')
