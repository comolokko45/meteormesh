from datetime import datetime
from zoneinfo import ZoneInfo
import sys
h=datetime.now(ZoneInfo("Europe/Istanbul")).hour
print("MORNING_WINDOW:", "ALLOW" if 5<=h<19 else "SKIP")
sys.exit(0 if 5<=h<19 else 1)
