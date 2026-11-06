#!/usr/bin/env python3
import hashlib, json, re, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
scope_path=Path('/home/meteor/.local/share/meteormesh/staging/rc157-20261102-fair-sync/ops/night-scope-20261101-final.json')
scope=json.loads(scope_path.read_text())
if scope.get('night_id')!='20261101-night':
    raise SystemExit('REFUSE: unexpected night scope')
start,end=scope['start_utc_ms'],scope['end_utc_ms']
pattern=re.compile(r'^[A-Za-z0-9_-]+_(\d{8}T\d{6}Z)_[0-9a-f]{12}$')
counts={'cam-ruby':0,'cam-surya':0,'cam-viva':0}
db=sqlite3.connect('file:/srv/meteor-data/meteorhub.sqlite3?mode=ro',uri=True)
rows=db.execute("SELECT device_id,segment_id FROM segments WHERE state='INGEST_VERIFIED'").fetchall()
for device,segment in rows:
    match=pattern.fullmatch(segment or '')
    if device not in counts or not match:
        continue
    stamp=datetime.strptime(match.group(1),'%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
    millis=int(stamp.timestamp()*1000)
    if start<=millis<end:
        counts[device]+=1
target='ruby_20261101T170538Z_cbbe0ddc8364'
row=db.execute("SELECT size_bytes,state,ffprobe_json,sha256 FROM segments WHERE device_id='cam-ruby' AND segment_id=?",(target,)).fetchone()
video=Path('/srv/meteor-data/cam-ruby')/(target+'.mp4')
expected={'cam-ruby':120,'cam-surya':120,'cam-viva':12}
print('20261101-night verified counts:',counts)
target_sha_ok=False
if row and row[1]=='INGEST_VERIFIED' and video.is_file() and video.stat().st_size==257584012:
    digest=hashlib.sha256()
    with video.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    target_sha_ok=digest.hexdigest().lower()==row[3].lower()
if counts!=expected or not row or row[0]!=257584012 or row[1]!='INGEST_VERIFIED' or not row[2] or not target_sha_ok:
    print('REFUSE: exact Ruby segment size/SHA/ffprobe or expected three-camera inventory is incomplete')
    raise SystemExit(4)
print('PASS: exact Ruby target matches DB size/SHA/ffprobe; 120/120 Ruby, 120/120 Surya, 12/12 Viva are present.')
