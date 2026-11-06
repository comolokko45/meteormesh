#!/usr/bin/env python3
"""Atomic empty DB publication; existing DB is never replaced."""
import os,sqlite3,subprocess,tempfile
from pathlib import Path
def initialize(root,schema,expected='444a8a6d-090a-456c-8a9d-d99665c32574'):
 root=Path(root)
 if root.is_symlink():raise RuntimeError('SYMLINK_ROOT')
 actual=subprocess.check_output(['findmnt','-n','-o','UUID','--mountpoint',str(root)],text=True).strip()
 if actual!=expected:raise RuntimeError('UUID_MISMATCH')
 destination=root/'meteorhub.sqlite3'
 if destination.exists() or destination.is_symlink():raise FileExistsError(destination)
 lock=root/'.meteor-morning.lock'
 if lock.is_symlink():raise RuntimeError('SYMLINK_LOCK')
 fd,tmp=tempfile.mkstemp(prefix='.meteorhub-init-',dir=root);os.close(fd)
 try:
  con=sqlite3.connect(tmp)
  try:
   con.executescript(schema);con.commit()
   if con.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('DB_CHECK_FAILED')
  finally:con.close()
  with open(tmp,'rb') as f:os.fsync(f.fileno())
  if not lock.exists():
   fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600);os.close(fd)
  # link() is atomic and refuses an existing destination, including a racer.
  os.link(tmp,destination)
  directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
  try:os.fsync(directory)
  finally:os.close(directory)
 finally:Path(tmp).unlink(missing_ok=True)
if __name__=='__main__':
 initialize('/srv/meteor-data',Path(__file__).with_name('empty-schema.sql').read_text())
 print('EMPTY_DATABASE_CREATED; no jobs or media restored')
