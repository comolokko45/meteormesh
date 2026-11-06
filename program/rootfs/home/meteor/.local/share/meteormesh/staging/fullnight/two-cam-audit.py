import json, sqlite3, re
from pathlib import Path
from collections import Counter
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

tz = ZoneInfo("Europe/Istanbul")
now = datetime.now(tz)
day = now.date() if now.hour >= 19 else now.date()-timedelta(days=1)
start = datetime.combine(day,time(19),tz)
end = datetime.combine(day+timedelta(days=1),time(5),tz)
night = f"{day:%Y%m%d}-night"

def stamp(seg):
    m = re.search(r'_(\d{8}T\d{6}Z)_[0-9a-f]{12}$',seg)
    if not m:
        return None
    try:
        t = datetime.strptime(m.group(1),"%Y%m%dT%H%M%SZ")
        return t.replace(tzinfo=timezone.utc).astimezone(tz)
    except ValueError:
        return None

scope_path = Path.home()/".local/share/meteormesh/staging/fullnight/night-scope-current.json"
scope = json.loads(scope_path.read_text())

print("GECE:",night)
print("SCOPE:",scope.get("night_id"),scope.get("status"))
print("SCOPE_ESLESMESI:",
      "OK" if scope.get("night_id")==night and scope.get("status")=="ARMED"
      else "BEKLENIYOR")

devs=("cam-surya","cam-ruby")
verified={d:set() for d in devs}
states={d:Counter() for d in devs}
successful={d:set() for d in devs}
old_failed=Counter()

db=sqlite3.connect(
 "file:/srv/meteor-data/meteorhub.sqlite3?mode=ro",
 uri=True
)

for dev,seg,sha in db.execute("""
 SELECT device_id,segment_id,sha256
 FROM segments WHERE state='INGEST_VERIFIED'
"""):
    t=stamp(seg)
    if dev in verified and t and start<=t<end:
        verified[dev].add((seg,sha))

for dev,seg,sha,state in db.execute("""
 SELECT device_id,segment_id,video_sha256,state FROM jobs
"""):
    if dev not in devs:
        continue
    t=stamp(seg)
    if t and start<=t<end:
        states[dev][state]+=1
        if state=="SUCCEEDED":
            successful[dev].add((seg,sha))
    elif t and t<start and state=="FAILED":
        old_failed[dev]+=1

db.close()

for dev in devs:
    print("\n===",dev,"===")
    print("DOGRULANMIS_MP4:",len(verified[dev]))
    print("ANALIZI_BASARILI_MP4:",
          len(verified[dev] & successful[dev]))
    print("IS_DURUMLARI:",dict(states[dev]))
    print("GECMIS_FAILED:",old_failed[dev])

print("\nGECE_KAPANDI:",now>=end)
print("NIHAI_KABUL: HENUZ_VERILMEDI")
print("OTOMATIK_SILME: KAPALI")
