import json, sqlite3, subprocess, hashlib, re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

root = Path("/srv/meteor-data")
scope_path = Path.home()/".local/share/meteormesh/staging/fullnight/night-scope-current.json"
expected_uuid = "444a8a6d-090a-456c-8a9d-d99665c32574"

def stop(message):
    print("FAIL_CLOSED:", message)
    raise SystemExit(0)

mount = subprocess.check_output(
    ["findmnt","-n","-T",str(root),"-o","UUID,FSTYPE,TARGET"],
    text=True).split()
if mount != [expected_uuid,"ext4",str(root)]:
    stop("HDD_UUID_OR_MOUNT")

if root.stat().st_dev == Path("/").stat().st_dev:
    stop("ROOT_FILESYSTEM")

if root.stat().st_mode & 0o170000 != 0o040000:
    stop("INVALID_DATA_DIRECTORY")

import shutil
if shutil.disk_usage(root).free < 100*1024**3:
    stop("FREE_SPACE_BELOW_100GIB")

scope = json.loads(scope_path.read_text())
now = datetime.now(ZoneInfo("Europe/Istanbul"))
evening = now.date() if now.hour >= 19 else now.date()-timedelta(days=1)
expected_night = evening.strftime("%Y%m%d") + "-night"

if scope.get("night_id") != expected_night:
    stop("WAIT_FOR_CURRENT_NIGHT_SCOPE")

if scope.get("status") != "ARMED":
    stop("SCOPE_NOT_ARMED")

start, end = scope["start_utc_ms"], scope["end_utc_ms"]
if not (isinstance(start,int) and isinstance(end,int) and start < end):
    stop("INVALID_SCOPE_WINDOW")

db = sqlite3.connect(
    f"file:{root}/meteorhub.sqlite3?mode=ro",uri=True)

checked = 0
for dev,seg,sha,size,probe in db.execute("""
 SELECT device_id,segment_id,sha256,size_bytes,ffprobe_json
 FROM segments WHERE state='INGEST_VERIFIED'
"""):
    if dev not in ("cam-surya","cam-ruby","cam-viva"):
        continue
    m = re.fullmatch(
        r"(surya|ruby|viva)_(\d{8}T\d{6}Z)_[0-9a-f]{12}",seg)
    if not m or "cam-"+m.group(1) != dev:
        continue
    t = datetime.strptime(m.group(2),"%Y%m%dT%H%M%SZ")
    ms = int(t.replace(tzinfo=timezone.utc).timestamp()*1000)
    if not start <= ms < end:
        continue
    if seg in scope.get("excluded_segments",{}).get(dev,{}):
        continue
    video = root/dev/(seg+".mp4")
    if video.is_symlink() or not video.is_file():
        continue
    if video.stat().st_size != size or not probe:
        continue
    h = hashlib.sha256()
    with video.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    if h.hexdigest() != sha:
        continue
    result = subprocess.run(
        ["ffprobe","-v","error","-show_entries",
         "format=duration:stream=codec_type,width,height",
         "-of","json",str(video)],
        capture_output=True,text=True,timeout=45)
    if result.returncode:
        continue
    info = json.loads(result.stdout)
    if float(info.get("format",{}).get("duration",0)) <= 0:
        continue
    if not any(s.get("codec_type") == "video"
               and s.get("width",0)>0 and s.get("height",0)>0
               for s in info.get("streams",[])):
        continue
    print("LOCAL_ONLY_PASS:",dev,seg)
    checked += 1
    if checked >= 3:
        break

db.close()
print("DRY_RUN_COMPLETE | local_checked:",checked)
print("PURGE_DISABLED | remote_and_sync_report_not_verified")
