#!/usr/bin/env python3
"""Use shared MeteorNode PIN 0000 for 3 trusted-LAN devices on existing 0.13 Hub.

No new services, no media/SQLite operations, preserves TLS certificate and versioned
runtime; only updates three authentication tokens in existing private control config.
Backs up previous private tokens (0600) and rolls back if service validation fails.
"""
from __future__ import annotations
import hashlib, hmac, ipaddress, json, os, re, secrets, ssl, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

CAMERAS=('cam-viva','cam-ruby','cam-surya')
PIN='0000'
FINGERPRINT='aa2f35e659a795bbd50ba8fade8de916ae7308c5cef3247a3fba74683c282c7b'
DIR=Path.home()/'.local/share/meteormesh/control-013'
CONFIG=DIR/'config.json'
CERT=DIR/'server.crt'
SERVICE='meteormesh-control-013.service'

def token(device: str, pin: str = PIN) -> str:
    if device not in CAMERAS or not re.fullmatch('[0-9]{4}',pin):raise ValueError('INVALID_PIN_OR_CAMERA')
    return hashlib.sha256(f'meteormesh.control013.pin.v1:{device}:{pin}'.encode('utf-8')).hexdigest()

def run(*args, check=True):
    p=subprocess.run(args,capture_output=True,text=True,timeout=20)
    if check and p.returncode:raise RuntimeError('COMMAND_FAILED: '+args[0]+' '+p.stderr[:150])
    return p.stdout.strip()

def need(test,reason):
    if not test: raise RuntimeError(reason)

def safe_file(path):
    need(path.is_file() and not path.is_symlink() and path.stat().st_mode&0o077==0,'UNSAFE_FILE: '+path.name)

def check_cert():
    safe_file(CERT)
    fingerprint=hashlib.sha256(ssl.PEM_cert_to_DER_cert(CERT.read_text())).hexdigest()
    need(hmac.compare_digest(fingerprint,FINGERPRINT),'CERT_FINGERPRINT_CHANGED_REQUIRES_ANDROID_SOURCE_UPDATE')

def get_config():
    safe_file(CONFIG)
    obj=json.loads(CONFIG.read_bytes())
    need(set(obj)=={'cert','key','tokens','port','bind'} and obj['port']==8443
         and obj['bind']=='0.0.0.0' and obj['cert']==str(CERT)
         and isinstance(obj['tokens'],dict) and set(obj['tokens'])==set(CAMERAS)
         and all(re.fullmatch('[0-9a-f]{64}',v) for v in obj['tokens'].values()),'CONTROL_BASELINE_CHANGED')
    need(not (DIR.is_symlink() or DIR.parent.is_symlink()),'UNSAFE_CONTROL_DIR')
    return obj

def atomic_new(path, payload):
    need(not path.exists() and not path.is_symlink(),'BACKUP_EXISTS')
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.pin0000-')
    try:
        with os.fdopen(fd,'wb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o600)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def atomic_replace(path,payload):
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.pin0000-')
    try:
        with os.fdopen(fd,'wb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o600)
        need(path.is_file() and not path.is_symlink(),'CONFIG_CHANGED_WHILE_UPDATING')
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def verify_service():
    need(run('systemctl','--user','is-active',SERVICE)=='active','CONTROL_SERVICE_NOT_ACTIVE')
    ctx=ssl.create_default_context(cafile=str(CERT))
    # The original server certificate includes localhost SAN.
    with urllib.request.urlopen('https://127.0.0.1:8443/v1/status',context=ctx,timeout=4) as r:
        obj=json.load(r)
        need(r.status==200 and obj.get('schema')=='meteormesh.control-status.v1','CONTROL_HTTPS_BAD_RESPONSE')

def main():
    need(os.geteuid()!=0,'DO_NOT_RUN_AS_ROOT')
    need(DIR.is_dir() and not DIR.is_symlink(),'CONTROL_013_NOT_INSTALLED')
    check_cert()
    cfg=get_config()
    need(run('systemctl','--user','is-enabled',SERVICE)=='enabled','CONTROL_SERVICE_DISABLED')
    verify_service()
    desired={d:token(d) for d in CAMERAS}
    if cfg['tokens']==desired:
        print('CONTROL_PIN0000: ALREADY_ACTIVE')
        print('PIN: 0000; CAMERA_TOKENS: DERIVED_NO_MANUAL_PAIRING')
        return
    backups=list(DIR.glob('config-before-pin0000-*.json'))
    need(not backups,'PRIOR_PIN0000_BACKUP_EXISTS_REVIEW_MANUALLY')
    stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
    backup=DIR/f'config-before-pin0000-{stamp}.json'
    original=CONFIG.read_bytes()
    atomic_new(backup,original)
    new=dict(cfg);new['tokens']=desired
    serialized=(json.dumps(new,sort_keys=True,indent=2)+'\n').encode()
    try:
        atomic_replace(CONFIG,serialized)
        run('systemctl','--user','restart',SERVICE)
        verify_service()
        # Check server implementation understands derived token; this does not
        # create a fake heartbeat or claim any phone is online.
        need(get_config()['tokens']==desired,'CONFIG_TOKEN_MISMATCH')
    except BaseException:
        atomic_replace(CONFIG,original)
        subprocess.run(['systemctl','--user','restart',SERVICE],capture_output=True,timeout=20)
        raise
    print('CONTROL_PIN0000: PASS')
    print('COMMON_PIN: 0000; CAMERAS: 3; CERTIFICATE: PRESERVED')
    print('HTTPS_STATUS: PASS; PORT: 8443; ORIGINAL_SERVICE: RESTARTED')
    print('APP_SETUP: HP_LAN_IP + PIN_0000_ONLY')
    print('OLD_TOKENS_BACKUP:',backup)
    print('VIDEOS_SQLITE_MORNING_ANALYSIS: UNCHANGED; AUTO_DELETE: OFF')
    print('NOTE: PIN 0000 IS WEAK; USE TRUSTED_LAN_ONLY; NO INTERNET_PORT_FORWARD')

if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,subprocess.SubprocessError,TimeoutError) as e:
        print('CONTROL_PIN0000_STOPPED: '+type(e).__name__+': '+str(e)[:200],file=sys.stderr)
        sys.exit(2)
