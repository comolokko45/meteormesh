#!/usr/bin/env python3
"""Verify/reassemble and restore program files. Never starts services or writes media."""
import argparse,base64,hashlib,io,json,os,tarfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
def assembled(name):
 p=HERE/name
 if p.exists():return p.read_bytes()
 parts=sorted(HERE.glob(name+'.part-*'))
 if not parts:raise RuntimeError('Missing '+name)
 return b''.join(x.read_bytes() for x in parts)
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');parser.add_argument('--root',default='/');parser.add_argument('--recovery-key',type=Path);parser.add_argument('--environment-dir',type=Path);args=parser.parse_args()
 checks={line.split('  ',1)[1]:line.split('  ',1)[0] for line in (HERE/'SHA256SUMS').read_text().splitlines()}
 for name,digest in checks.items():
  assert hashlib.sha256(assembled(name)).hexdigest()==digest,'Hash mismatch: '+name
 manifest=json.loads(assembled('manifest.json'));expected={r['path']:r['sha256'] for r in manifest['files']}
 files={};private_files=set()
 with tarfile.open(fileobj=io.BytesIO(assembled('program.tar.gz')),mode='r:gz') as tar:
  for member in tar:
   assert member.isfile() and member.name in expected,'Unexpected archive entry'
   raw=tar.extractfile(member).read();assert hashlib.sha256(raw).hexdigest()==expected[member.name]
   if member.name.startswith('rootfs/'):files[member.name[7:]]=raw
   elif member.name.startswith('environment/') and args.environment_dir:
    destination=args.environment_dir/Path(member.name).name
    destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(raw)
 if args.recovery_key:
  from cryptography.hazmat.primitives.ciphers.aead import AESGCM
  key=base64.urlsafe_b64decode(args.recovery_key.read_text().strip());blob=assembled('private-state.aesgcm');assert blob[:6]==b'MMGCM1'
  plain=AESGCM(key).decrypt(blob[6:18],blob[18:],b'MeteorMesh program backup v1')
  with tarfile.open(fileobj=io.BytesIO(plain),mode='r:gz') as tar:
   for member in tar:
    assert member.isfile() and member.name.startswith('vault/')
    private_files.add(member.name[6:]);files[member.name[6:]]=tar.extractfile(member).read()
  blob=assembled('android-delivery.aesgcm');android=AESGCM(key).decrypt(blob[6:18],blob[18:],b'MeteorMesh program backup v1')
  files['home/meteor/MeteorMesh-Android-Delivery.zip']=android
  private_files.add('home/meteor/MeteorMesh-Android-Delivery.zip')
 root=Path(args.root).resolve()
 targets=[]
 for name,raw in files.items():
  relative=Path(name);assert not relative.is_absolute() and '..' not in relative.parts
  assert name.startswith(('home/meteor/','opt/meteormesh/'))
  target=root/relative
  assert target.resolve().is_relative_to(root),'Unsafe destination'
  if target.exists() and target.read_bytes()!=raw:raise RuntimeError('Existing different file; refusing overwrite: '+str(target))
  targets.append((target,raw,name in private_files))
 print('VERIFIED',len(targets),'files; credentials restored:',bool(args.recovery_key))
 if not args.apply:return
 if root==Path('/'):
  if os.geteuid()!=0:raise RuntimeError('Real root restore requires sudo')
  import pwd
  user=pwd.getpwnam('meteor')
 for target,raw,private in targets:
  target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
  target.chmod(0o600 if private or '/.config/meteormesh/' in str(target) or '/control-013/' in str(target) or '/.android/' in str(target) else 0o644)
  if root==Path('/') and str(target).startswith('/home/meteor/'):
   os.chown(target,user.pw_uid,user.pw_gid)
   parent=target.parent
   while str(parent).startswith('/home/meteor/'):
    os.chown(parent,user.pw_uid,user.pw_gid);parent=parent.parent
 for relative in ('home/meteor/.local/state/meteormesh','home/meteor/.local/share/meteormesh/db-backups','home/meteor/.local/share/meteormesh/receipts','home/meteor/.config/meteormesh','home/meteor/.local/share/meteormesh/control-013','home/meteor/.android'):
  directory=root/relative;directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o700)
  if root==Path('/'):os.chown(directory,user.pw_uid,user.pw_gid)
 print('RESTORED; no services enabled/started; finish INSTALL.md')
if __name__=='__main__':main()
