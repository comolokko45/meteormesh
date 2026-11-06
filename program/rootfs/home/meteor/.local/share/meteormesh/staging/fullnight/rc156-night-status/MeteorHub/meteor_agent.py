#!/usr/bin/env python3
"""MeteorAgent 0.2: truly read-only local diagnostic interface.

An LLM connector can later consume this JSON. This program is deliberately
NOT an LLM; it offers no arbitrary commands or file-changing operations.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def read_only_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError("MeteorHub SQLite database is missing or unsafe")
    # mode=ro forbids database creation and writes by this process.
    from urllib.parse import quote
    uri = "file:" + quote(str(path.resolve())) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def answer(conn: sqlite3.Connection, question: str) -> dict:
    """Fixed SELECT queries; no model-generated SQL and no tool execution."""
    if question == "status":
        return {
            "segments": dict(conn.execute("SELECT state, COUNT(*) FROM segments GROUP BY state")),
            "jobs": dict(conn.execute("SELECT state, COUNT(*) FROM jobs GROUP BY state")),
            "detector_runs": conn.execute("SELECT COUNT(*) FROM detector_runs").fetchone()[0],
            "autonomous_purge_enabled": False,
            "agent_can_delete": False,
        }
    if question == "cameras":
        return {"cameras": [{"id": row[0], "ingested_segments": row[1],
                             "bytes": row[2]} for row in conn.execute(
                                 "SELECT device_id, COUNT(*), COALESCE(SUM(size_bytes),0) "
                                 "FROM segments GROUP BY device_id ORDER BY device_id")]}
    if question == "pending":
        return {"jobs": [{"job_id": row[0], "camera": row[1], "segment": row[2],
                          "state": row[3], "attempts": row[4], "error": row[5]}
                         for row in conn.execute(
                             "SELECT id, device_id, segment_id, state, attempts, last_error "
                             "FROM jobs WHERE state!='SUCCEEDED' ORDER BY id LIMIT 100")]}
    if question == "retention":
        return {"auto_purge": False, "retention_receipts": "NOT_IMPLEMENTED",
                "session_analysis_closed": "NOT_IMPLEMENTED",
                "reason": "Never delete originals before verified archival, analysis closure and approved policy."}
    if question == "briefing":
        total, bytes_total = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes),0) FROM segments WHERE state='INGEST_VERIFIED'"
        ).fetchone()
        counts = dict(conn.execute("SELECT state, COUNT(*) FROM jobs GROUP BY state"))
        failed = counts.get("FAILED", 0)
        return {
            "dil": "tr", "kaynak": "yerel_sqlite_salt_okunur",
            "dogrulanmis_video_sayisi": total,
            "dogrulanmis_video_bayti": bytes_total,
            "bekleyen_analiz": counts.get("WAITING", 0),
            "calisan_analiz": counts.get("RUNNING", 0),
            "tamamlanan_analiz": counts.get("SUCCEEDED", 0),
            "basarisiz_analiz": failed,
            "uyari": ("Başarısız analiz işleri kontrol edilmeli." if failed else
                     "Veritabanında başarısız analiz işi görünmüyor."),
            "gercek_meteor_sayisi": "BILINMIYOR_INCELEME_GEREKLI",
            "otomatik_silme": False,
            "yapay_zeka_modeli_aktif": False,
        }
    raise ValueError("Question must be one of: status, cameras, pending, retention, briefing")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("question", choices=["status", "cameras", "pending", "retention", "briefing"])
    args = parser.parse_args(argv)
    with read_only_connection(args.data_dir / "meteorhub.sqlite3") as conn:
        print(json.dumps(answer(conn, args.question), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, FileNotFoundError, sqlite3.Error) as exc:
        print(f"MeteorAgent: {exc}", file=sys.stderr)
        sys.exit(2)
