#!/usr/bin/env python3
"""MeteorNode 0.13 HP G62 TLS control endpoint. Additive, versioned, recoverable.

Does NOT modify MeteorHub, media, SQLite, systemd timers, phone APK, or disk content.
Starts ONE new user service with a private TLS key and per-device random tokens.
"""
from __future__ import annotations
import base64,hashlib,io,json,os,secrets,shutil,socket,ssl,stat,subprocess,sys,tempfile,time,urllib.request,zipfile
from pathlib import Path

PAYLOAD_SHA256='c110f2eef1231d69143faec468505cb1afe7d32516bb323389b0e4a65ba55ec8'
PAYLOAD_B64='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'
HOME=Path.home()
RELEASE=HOME/'.local/share/meteormesh/releases/control-013-20261101'
KEYDIR=HOME/'.local/share/meteormesh/control-013'
UNIT=HOME/'.config/systemd/user/meteormesh-control-013.service'
SERVICE='meteormesh-control-013.service'
CAMERAS=('cam-viva','cam-ruby','cam-surya')

def proc(*args,check=True,timeout=20):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if check and p.returncode:raise RuntimeError('COMMAND_FAILED '+args[0]+': '+(p.stderr or p.stdout)[:150])
    return p.stdout.strip()

def need(cond,why):
    if not cond:raise RuntimeError(why)

def verify_prerequisites():
    need(os.geteuid()!=0,'DO_NOT_RUN_AS_ROOT')
    need(not (RELEASE.exists() or RELEASE.is_symlink() or KEYDIR.exists() or KEYDIR.is_symlink() or UNIT.exists() or UNIT.is_symlink()),'INSTALL_ALREADY_EXISTS')
    live=proc('systemctl','--user','show','meteormesh-analyze.service','-p','ExecStart','--value')
    need('--phase analyze' in live and '--max-runtime-hours 16' in live,'RC155_ANALYSIS_BASELINE_MISSING')
    need(proc('systemctl','--user','is-enabled','meteormesh-morning.timer')=='enabled','MORNING_TIMER_NOT_ENABLED')
    need(shutil.which('openssl') is not None,'OPENSSL_NOT_INSTALLED')
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        s.bind(('0.0.0.0',8443))
    raw=base64.b64decode(PAYLOAD_B64,validate=True)
    need(hashlib.sha256(raw).hexdigest()==PAYLOAD_SHA256,'EMBEDDED_PACKAGE_INTEGRITY_FAILED')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        need(set(z.namelist())=={'server.py','pairing.py','test_control.py'},'UNEXPECTED_SOURCE_FILE')
        need(z.testzip() is None,'ZIP_CRC_FAILED')
        sources={name:z.read(name) for name in z.namelist()}
    for name,data in sources.items():
        compile(data,name,'exec')
    return sources

def install():
    sources=verify_prerequisites()
    releases=RELEASE.parent
    releases.mkdir(parents=True,exist_ok=True)
    KEYDIR.parent.mkdir(parents=True,exist_ok=True)
    UNIT.parent.mkdir(parents=True,exist_ok=True)
    os.umask(0o077)
    temp=Path(tempfile.mkdtemp(prefix='.control-013-',dir=releases))
    staged=temp/'release';staged.mkdir(mode=0o700)
    confdir=temp/'conf';confdir.mkdir(mode=0o700)
    created=[]
    try:
        for name,data in sources.items():
            dest=staged/name;dest.write_bytes(data);dest.chmod(0o600)
        # Tests run with cwd=staged below for installed source; no network needed.
        run=subprocess.run(['/usr/bin/python3','-B','-m','unittest','-q','test_control'],cwd=staged,capture_output=True,text=True,timeout=15)
        need(run.returncode==0 and 'Ran 8 tests' in run.stderr+run.stdout,'EMBEDDED_TESTS_FAILED '+(run.stderr+run.stdout)[-130:])
        cert,key=confdir/'server.crt',confdir/'server.key'
        proc('openssl','req','-x509','-newkey','rsa:3072','-sha256','-nodes','-days','730',
             '-keyout',str(key),'-out',str(cert),'-subj','/CN=MeteorMeshControl013',
             '-addext','subjectAltName=IP:127.0.0.1',timeout=30)
        key.chmod(0o600);cert.chmod(0o600)
        tokens={d:secrets.token_hex(32) for d in CAMERAS}
        config={'cert':str(KEYDIR/'server.crt'),'key':str(KEYDIR/'server.key'),
                'tokens':tokens,'port':8443,'bind':'0.0.0.0'}
        data=(json.dumps(config,sort_keys=True,indent=2)+'\n').encode()
        (confdir/'config.json').write_bytes(data)
        (confdir/'config.json').chmod(0o600)
        certder=ssl.PEM_cert_to_DER_cert(cert.read_text())
        fingerprint=hashlib.sha256(certder).hexdigest()
        unit="""[Unit]
Description=MeteorMesh 0.13 authenticated phone heartbeat (no video access)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -B @@RELEASE@@/server.py --config @@KEYDIR@@/config.json
WorkingDirectory=@@RELEASE@@
Restart=on-failure
RestartSec=30
TimeoutStartSec=20s
NoNewPrivileges=yes
UMask=0077

[Install]
WantedBy=default.target
""".replace('@@RELEASE@@',str(RELEASE)).replace('@@KEYDIR@@',str(KEYDIR))
        (temp/'service').write_text(unit)
        # Publish both versioned data dirs by rename, never overwrite.
        os.rename(staged,RELEASE);created.append(RELEASE)
        os.rename(confdir,KEYDIR);created.append(KEYDIR)
        os.replace(temp/'service',UNIT);UNIT.chmod(0o644);created.append(UNIT)
        proc('systemctl','--user','daemon-reload')
        proc('systemctl','--user','enable','--now',SERVICE,timeout=18)
        # Immediate status may race against service initialization; query active state.
        need(proc('systemctl','--user','is-active',SERVICE)=='active','CONTROL_SERVICE_NOT_ACTIVE')
        ok=False
        for _ in range(12):
            try:
                ctx=ssl.create_default_context(cafile=str(KEYDIR/'server.crt'))
                with urllib.request.urlopen('https://127.0.0.1:8443/v1/status',context=ctx,timeout=2) as reply:
                    result=json.load(reply)
                    ok=(reply.status==200 and result.get('schema')=='meteormesh.control-status.v1')
            except (OSError,ValueError,ssl.SSLError):
                time.sleep(0.25)
            if ok:break
        need(ok,'LOCAL_TLS_STATUS_TEST_FAILED')
        # Only HTTPS over localhost can reveal status; all heartbeats unavailable until phone is paired.
        print('MM013_CONTROL_HUB: PASS')
        print('HTTPS_PORT: 8443; TRUST: CERT_SHA256_PIN + DEVICE_TOKEN')
        print('TLS_CERT_SHA256:',fingerprint)
        print('CAMERAS: cam-viva cam-ruby cam-surya (unpaired until APK setup)')
        print('CONTROL: HEARTBEAT + RESTART_LISTENER_ONLY')
        print('LOCAL_STATUS: curl --cacert '+str(KEYDIR/'server.crt')+' https://127.0.0.1:8443/v1/status')
        print('LOCAL_PAIR_INFO: python3 '+str(RELEASE/'pairing.py')+' cam-ruby')
        print('VIDEOS_SQLITE_MORNING_ANALYSIS: UNCHANGED; AUTO_DELETE: OFF')
        print('ROLLBACK: systemctl --user disable --now '+SERVICE+' && rm '+str(UNIT)+' && systemctl --user daemon-reload')
    except BaseException:
        subprocess.run(['systemctl','--user','disable','--now',SERVICE],capture_output=True,timeout=12)
        for path in reversed(created):
            if path.is_dir() and not path.is_symlink():shutil.rmtree(path)
            else:path.unlink(missing_ok=True)
        subprocess.run(['systemctl','--user','daemon-reload'],capture_output=True,timeout=12)
        raise
    finally:shutil.rmtree(temp,ignore_errors=True)

if __name__=='__main__':
    try:install()
    except (RuntimeError,ValueError,OSError,subprocess.SubprocessError,zipfile.BadZipFile) as e:
        print('MM013_CONTROL_STOPPED: '+type(e).__name__+': '+str(e)[:200],file=sys.stderr)
        sys.exit(2)
