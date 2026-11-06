#!/usr/bin/env python3
"""Read-only diagnosis for the single previously pulled Viva MP4; never writes."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

sid = 'viva_20261029T152451Z_138f2c357988'
root = Path('/srv/meteor-data')
video = root/'cam-viva'/f'{sid}.mp4'
partial = root/'cam-viva'/f'{sid}.mp4.part'
db_file = root/'meteorhub.sqlite3'
print('VIDEO', str(video))
print('VIDEO_BYTES', video.stat().st_size if video.is_file() else 'NOT_PRESENT')
print('PARTIAL_BYTES', partial.stat().st_size if partial.is_file() else 'NOT_PRESENT')
try:
    with sqlite3.connect('file:' + str(db_file) + '?mode=ro', uri=True, timeout=2) as db:
        db.execute('PRAGMA query_only=ON')
        row = db.execute('SELECT state,ffprobe_json FROM segments WHERE device_id=? AND segment_id=?', ('cam-viva',sid)).fetchone()
        print('DB_STATE', row[0] if row else 'NO_ROW')
        if row and row[1]:
            print('DB_FFPROBE_DIAGNOSTIC', row[1][:1200])
except (sqlite3.Error, OSError) as exc:
    print('DB_READ_ONLY_ERROR', str(exc))
if not video.is_file() or video.is_symlink():
    print('STOP_NO_VERIFIED_VIDEO_FILE')
    sys.exit(2)
cmd = ['ffprobe','-v','error','-show_entries',
       'format=duration,size:stream=codec_type,codec_name,width,height',
       '-of','json',str(video)]
try:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=45, check=False)
except (OSError, subprocess.TimeoutExpired) as exc:
    print('DIRECT_FFPROBE_ERROR', str(exc))
    sys.exit(2)
print('DIRECT_FFPROBE_EXIT', out.returncode)
if out.returncode:
    print('DIRECT_FFPROBE_STDERR', out.stderr[:1000])
    sys.exit(2)
try:
    obj = json.loads(out.stdout)
    duration = float(obj.get('format',{}).get('duration',0))
    streams = obj.get('streams', [])
    ok = duration>0 and any(x.get('codec_type')=='video' and x.get('width',0)>0 and x.get('height',0)>0 for x in streams)
    print('DIRECT_FFPROBE_DURATION',duration)
    print('DIRECT_FFPROBE_VIDEO',next((x for x in streams if x.get('codec_type')=='video'),{}))
    print('DIRECT_FFPROBE', 'OK' if ok else 'INVALID_MEDIA')
    sys.exit(0 if ok else 2)
except (ValueError,TypeError,AttributeError,KeyError) as exc:
    print('DIRECT_FFPROBE_INVALID_JSON',str(exc))
    sys.exit(2)
