#!/usr/bin/env python3
"""Create a new empty MeteorHub DB only on the exact project disk; no overwrites."""
import os,sqlite3,subprocess
from pathlib import Path
root=Path('/srv/meteor-data')
assert not root.is_symlink()
expected='444a8a6d-090a-456c-8a9d-d99665c32574'
uuid=subprocess.check_output(['findmnt','-n','-o','UUID','--mountpoint',str(root)],text=True).strip()
assert uuid==expected,'Project HDD UUID must be verified first'
db=root/'meteorhub.sqlite3'
fd=os.open(db,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600);os.close(fd)
con=sqlite3.connect(db)
try:
 con.executescript(Path(__file__).with_name('empty-schema.sql').read_text())
 con.commit();assert con.execute('PRAGMA quick_check').fetchone()[0]=='ok'
finally:con.close()
lock=root/'.meteor-morning.lock'
if not lock.exists():
 fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600);os.close(fd)
print('EMPTY_DATABASE_CREATED; no jobs or media restored')
