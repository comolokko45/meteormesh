#!/usr/bin/python3
"""Allow bounded analysis rounds only inside the configured local daytime window."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from zoneinfo import ZoneInfo


def minutes(value: str) -> int:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("time must use HH:MM") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise argparse.ArgumentTypeError("time must use HH:MM")
    return hour * 60 + minute


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timezone", default="Europe/Istanbul")
    parser.add_argument("--start", type=minutes, default=330)
    parser.add_argument("--stop-before", type=minutes, default=1130)
    args = parser.parse_args()

    now = dt.datetime.now(ZoneInfo(args.timezone))
    current = now.hour * 60 + now.minute
    allowed = args.start <= current < args.stop_before
    state = "ALLOW" if allowed else "SKIP"
    print(
        f"ANALYSIS_WINDOW: {state} local={now.isoformat(timespec='seconds')} "
        f"window={args.start // 60:02d}:{args.start % 60:02d}-"
        f"{args.stop_before // 60:02d}:{args.stop_before % 60:02d}"
    )
    return 0 if allowed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ANALYSIS_WINDOW_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(255)
