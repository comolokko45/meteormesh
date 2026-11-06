#!/usr/bin/env python3
"""Read-only HP G62 HDD identity/permissions preflight for MM-01.

Does NOT create files, touch SQLite, read videos, mount/unmount devices,
modify services, or activate the morning timer. For production review only.
"""
from __future__ import annotations
import json
from storage_guard import GuardedStorage, StorageDenied, StoragePolicy


def probe(storage_factory=GuardedStorage) -> dict:
    with storage_factory(StoragePolicy()) as guard:
        ev = guard.verify()
        return {"status": "VERIFIED_READ_ONLY_PREFLIGHT",
                "mountpoint": guard.policy.root,
                "fstype": ev.fstype,
                "uuid": ev.uuid,
                "read_only": ev.read_only,
                "same_mount": ev.is_exact_mount,
                "free_bytes": ev.free_bytes,
                "writes_performed": 0,
                "production_activated": False}


def main() -> int:
    try:
        result = probe()
    except (OSError, StorageDenied) as exc:
        print(json.dumps({"status": "DENIED", "error": str(exc),
                          "writes_performed": 0,
                          "production_activated": False}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
