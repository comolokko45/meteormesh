import hashlib,io,json,tarfile,shutil,subprocess
from pathlib import Path
root=Path(__file__).parent;repo=Path('MeteorMesh-GitHub-Preparation-2026-11-06/repository');stage=root/'publish';stage.mkdir(exist_ok=True)
items=[];patch=[]
def add(source,path):
 raw=Path(source).read_bytes();items.append({'path':path,'raw':raw})
for p in root.glob('*.py'):
 path='program/rootfs/home/meteor/.local/share/meteormesh/releases/rc223-p1-fixes-20261106/'+p.name
 add(p,path);patch.append((path[len('program/'):],p.read_bytes()))
overrides={
'home/meteor/.config/systemd/user/meteormesh-status.service.d/~rc223-p1.conf':'[Service]\nExecStartPost=\nExecStartPost=-/usr/bin/python3 -B /home/meteor/.local/share/meteormesh/releases/rc223-p1-fixes-20261106/meteor-heartbeat-watch.py --commit --quiet --allow-safe-listener-recovery\nExecStartPost=-/usr/bin/python3 -B /home/meteor/.local/share/meteormesh/releases/rc223-p1-fixes-20261106/meteor-ops-journal.py\n',
'home/meteor/.config/systemd/user/meteormesh-analyze.service.d/~rc223-p1.conf':'[Service]\nExecStopPost=\nExecStopPost=/usr/bin/python3 -B /home/meteor/.local/share/meteormesh/releases/rc223-p1-fixes-20261106/cleanup_gate.py\n'}
for p,text in overrides.items():
 raw=text.encode();items.append({'path':'program/rootfs/'+p,'raw':raw});patch.append(('rootfs/'+p,raw))
snap=repo/'snapshots/g62-20261106'
report=Path('METEORMESH_RC223_YEDI_P1_DUZELTME_2026-11-06.md')
add(report,'program/SECURITY_RC223.md')
install=snap/'INSTALL.md'
text=install.read_text(encoding='utf-8')
if '## RC223 security update' not in text:
 install.write_text(text+'\n\n## RC223 security update\n\nUse the current restore.py and SHA256SUMS. It verifies and applies security-update-rc223.tar.gz after the base archive. The base archive alone is historical. See ../../program/SECURITY_RC223.md for all seven fixes. Phones require the new MeteorNode0.13.7-security1 APK; the G62 patch does not update phones automatically.\n',encoding='utf-8')
add(install,'snapshots/g62-20261106/INSTALL.md')
for path in ('README.md','program/README.md'):
 raw=subprocess.check_output(['git','-C',str(repo),'show','b45497e557a762d9a16a435c85506f226c749405:'+path])
 link='program/SECURITY_RC223.md' if path=='README.md' else 'SECURITY_RC223.md'
 items.append({'path':path,'raw':raw+('\n\n## RC223 security corrections\n\nRead ['+link+']('+link+'). Corrected G62 hooks/policy and restore tools are active. MeteorNode0.13.7-security1 source and APK are supplied; phone installation remains pending.\n').encode()})
for name in ('restore.py','initialize_empty_db.py'):shutil.copyfile(root/name,snap/name);add(root/name,'snapshots/g62-20261106/'+name)
archive=snap/'security-update-rc223.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
 for path,raw in patch:
  info=tarfile.TarInfo(path);info.size=len(raw);info.mode=0o644;tar.addfile(info,io.BytesIO(raw))
add(archive,'snapshots/g62-20261106/'+archive.name)
old=(snap/'SHA256SUMS').read_text().splitlines();checks={x.split('  ',1)[1]:x.split('  ',1)[0] for x in old}
for name in ('restore.py','initialize_empty_db.py',archive.name,'INSTALL.md'):checks[name]=hashlib.sha256((snap/name).read_bytes()).hexdigest()
(snap/'SHA256SUMS').write_text(''.join(h+'  '+n+'\n' for n,h in sorted(checks.items())))
add(snap/'SHA256SUMS','snapshots/g62-20261106/SHA256SUMS')
android=Path('MeteorNode-0.13.7-security1')
for p in android.rglob('*'):
 if p.is_file() and not any(x in p.parts for x in ('build','.gradle','.git')):add(p,'program/android/MeteorNode-0.13.7-security1/'+str(p.relative_to(android)).replace('\\','/'))
add(root/'dist/MeteorNode-0.13.7-security1.apk','program/android/MeteorNode-0.13.7-security1/apk/MeteorNode-0.13.7-security1.apk')
metadata=[]
for i,item in enumerate(items):
 raw=item.pop('raw');p=stage/f'{i:03}.data';p.write_bytes(raw);metadata.append({'source':str(p.resolve()),'bytes':len(raw),'sha':hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),**item})
(root/'upload.json').write_text(json.dumps(metadata),encoding='utf-8');print(len(metadata),'files prepared')
