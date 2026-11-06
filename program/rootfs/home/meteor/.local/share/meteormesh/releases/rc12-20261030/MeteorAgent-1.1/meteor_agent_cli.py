#!/usr/bin/env python3
"""MeteorAgent 1.1 observe-only CLI. Reads existing status JSON, never changes it.

Usage: python3 meteor_agent_cli.py --input saved-status.json --format text
       other-read-only-producer | python3 meteor_agent_cli.py --input - --format json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_engine import evaluate
from agent_review import make_review

MAX_INPUT_BYTES = 1024 * 1024


def parse_input(stream) -> object:
    raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("Status JSON exceeds 1 MiB limit")
    return json.loads(raw)


def render_text(result: dict) -> str:
    lines = [f"MeteorAgent 1.1 [OBSERVE] — {result['level']}"]
    if result["causes"]:
        for item in result["causes"]:
            lines.append(f"- {item['code']}: {item['observation']}")
            lines.append(f"  Öneri: {item['recommendation']}")
    else:
        lines.append("Kritik neden belirlenmedi; gece kaydı veya bilimsel kabul kanıtlanmış değildir.")
    lines.extend([f"Olay anahtarı: {result['incident_fingerprint']}",
                  "Yan etkili eylem: YOK | Zamanlayıcı yönetimi: YOK | Silme: YASAK"])
    if "review" in result:
        r = result["review"]
        lines.extend([f"Sonraki adım: {r['operator_next_step']}",
                      f"Kuyruk: {r['hub_queue_counts']}"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", default="-", help="Existing status JSON path, or '-' for stdin")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--previous-fingerprint", help="Optional prior incident key; no state is written")
    args = p.parse_args(argv)
    try:
        if args.input == "-":
            snapshot = parse_input(sys.stdin)
        else:
            source = Path(args.input)
            if source.stat().st_size > MAX_INPUT_BYTES:
                raise ValueError("Status JSON exceeds 1 MiB limit")
            with source.open("r", encoding="utf-8") as stream:
                snapshot = parse_input(stream)
        result = evaluate(snapshot, previous_fingerprint=args.previous_fingerprint)
        result["review"] = make_review(snapshot, result)
    except (OSError, ValueError, json.JSONDecodeError, UnicodeError) as exc:
        # No unsanitized filename/exception echo to logs.
        print("MeteorAgent: input not readable or invalid JSON; no action executed", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_text(result))
    return 0 if result["level"] in ("OBSERVED_OK", "INCOMPLETE") else 1


if __name__ == "__main__":
    sys.exit(main())
