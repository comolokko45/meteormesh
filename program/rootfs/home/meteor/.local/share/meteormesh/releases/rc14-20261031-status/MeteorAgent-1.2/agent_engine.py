"""MeteorAgent 1.0: pure, read-only decision layer over existing status JSON.

This module never reads recordings or databases, accesses the network, runs shell,
or performs side effects. Status collection is deliberately NOT reimplemented.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

SCHEMA = "meteormesh.local-status.v1"
AGENT_SCHEMA = "meteormesh.agent-observe.v1"
EXPECTED_UUID = "444a8a6d-090a-456c-8a9d-d99665c32574"
MAX_EVIDENCE_AGE_SECONDS = 90 * 60
FREE_SPACE_NOTICE_GIB = 150  # Planning only: this does NOT block existing jobs.

# Codes are not executable commands. The recommendations are intentionally static.
CAUSES = {
    "STORAGE_UNVERIFIED": ("CRITICAL", "HDD bağlantısı, UUID veya dosya sistemi doğrulanmadı.", "MeteorHub yazma işlemlerini başlatma; mount/UUID durumunu operatör incelesin."),
    "TIMER_STATE_UNKNOWN": ("WARNING", "09:00 zamanlayıcısının etkin olduğu doğrulanamadı.", "RC1.4 sabah zamanlayıcısını operatör kontrol etsin; Agent servis değiştirmez."),
    "LOW_SPACE": ("WARNING", "Planlanan büyük aktarım için HDD boş alanı düşük.", "Yeni büyük aktarımı planlama; saklama veya harici arşiv kararı operatöre ait."),
    "DB_READ_ERROR": ("WARNING", "MeteorHub SQLite durumu okunamadı.", "Disk doğrulanmışsa salt okunur DB/izin incelemesi yap; veritabanını sıfırlama."),
    "FAILED_JOBS": ("WARNING", "İş kuyruğunda FAILED kayıtları bulunuyor.", "İş nedenlerini incele; MM-01 tamamlanmadan otomatik yeniden deneme yapma."),
    "PARTIAL_NIGHT": ("WARNING", "Son sabah raporu kısmi tamamlanma bildiriyor.", "Eksik kayıtları raporla; geceyi tamamlanmış sayma."),
    "CLOCK_UNSYNCED": ("WARNING", "Sunucu saat senkronizasyonu doğrulanmadı.", "UTC güvenilirliğini operatör incelemeli; saat ayarını otomatik değiştirme."),
    "MEMORY_LOW": ("WARNING", "Sunucunun kullanılabilir belleği düşük.", "Tek analiz işçisi politikasını koru; yeni iş ekleme."),
    "STATUS_ISSUES": ("WARNING", "Durum aracının açıklanmayan ek uyarıları mevcut.", "Orijinal durum raporunu incele; bilinmeyen uyarıyı otomatik düzeltme."),
    "EVIDENCE_STALE": ("WARNING", "Durum raporunun zamanı güncel veya güvenilir değil.", "Yeni salt okunur kanıt olmadan sistemin sağlıklı olduğunu varsayma."),
    "EVIDENCE_INVALID": ("CRITICAL", "Durum raporu geçerli şemaya veya güvenlik beyanına uymuyor.", "Kaynağı doğrula; hiçbir otomatik eylem başlatma."),
}
PRIORITY = ["EVIDENCE_INVALID", "STORAGE_UNVERIFIED", "TIMER_STATE_UNKNOWN", "EVIDENCE_STALE",
            "LOW_SPACE", "DB_READ_ERROR", "FAILED_JOBS", "PARTIAL_NIGHT",
            "CLOCK_UNSYNCED", "MEMORY_LOW", "STATUS_ISSUES"]


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    x = float(value)
    return x if x == x and abs(x) != float("inf") else None


def _read_count(collection: Any, key: str) -> int:
    if not isinstance(collection, Mapping):
        return 0
    n = collection.get(key)
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0


def evaluate(snapshot: Any, *, now: datetime | None = None,
             previous_fingerprint: str | None = None) -> dict[str, Any]:
    """Deterministic classification. Input/output JSON only, no side effects.

    A previous fingerprint can be passed by an external operator/caller for
    duplicate-alert suppression; no disk state and no secondary SQLite queue.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    found: set[str] = set()
    valid = (isinstance(snapshot, Mapping) and snapshot.get("schema") == SCHEMA
             and snapshot.get("read_only") is True
             and snapshot.get("runs_detector") is False
             and snapshot.get("can_delete") is False)
    if not valid:
        found.add("EVIDENCE_INVALID")
        checked = None
        age = None
        hdd_ok = False
    else:
        checked = _utc(snapshot.get("checked_tr"))
        age = (now - checked).total_seconds() if checked else None
        if age is None or age < -120 or age > MAX_EVIDENCE_AGE_SECONDS:
            found.add("EVIDENCE_STALE")
        hdd = snapshot.get("hdd")
        hdd = hdd if isinstance(hdd, Mapping) else {}
        # A healthy status string is never sufficient to establish mount identity.
        hdd_ok = (hdd.get("path") == "/srv/meteor-data"
                  and hdd.get("is_mount") is True
                  and hdd.get("fstype") == "ext4"
                  and hdd.get("uuid") == EXPECTED_UUID
                  and hdd.get("mount_verification") != "UNKNOWN")
        if not hdd_ok:
            found.add("STORAGE_UNVERIFIED")
        timer = snapshot.get("morning_timer")
        if not isinstance(timer, Mapping):
            found.add("TIMER_STATE_UNKNOWN")
        elif (timer.get("enabled") not in ("enabled", "enabled-runtime")
              or timer.get("active") != "active"):
            found.add("TIMER_STATE_UNKNOWN")
        db = snapshot.get("database")
        db = db if isinstance(db, Mapping) else {}
        if hdd_ok:
            free = _float(hdd.get("free_gib"))
            if free is None:
                found.add("STORAGE_UNVERIFIED")
                hdd_ok = False
            elif free < FREE_SPACE_NOTICE_GIB:
                found.add("LOW_SPACE")
            if db.get("state") not in ("READ_ONLY_OK", "NOT_CREATED"):
                found.add("DB_READ_ERROR")
            if _read_count(db.get("jobs"), "FAILED"):
                found.add("FAILED_JOBS")
            morning = snapshot.get("last_morning")
            if isinstance(morning, Mapping) and morning.get("outcome") == "PARTIAL_REVIEW_REQUIRED":
                found.add("PARTIAL_NIGHT")
        if snapshot.get("ntp_synchronized") == "no":
            found.add("CLOCK_UNSYNCED")
        mem = snapshot.get("memory")
        if isinstance(mem, Mapping):
            avail = _float(mem.get("available_gib"))
            if avail is not None and avail < 0.35:
                found.add("MEMORY_LOW")
        raw_issues = snapshot.get("issues")
        if not isinstance(raw_issues, list) or len(raw_issues) > 200:
            found.add("EVIDENCE_INVALID")
        elif raw_issues:
            known = {"HDD_NOT_MOUNTED", "HDD_UNEXPECTED_FILESYSTEM", "HDD_UNEXPECTED_UUID",
                     "HDD_MOUNT_SOURCE_UNVERIFIED", "HDD_FREE_UNDER_5_GIB", "HDD_FREE_LOW",
                     "SSD_FREE_UNDER_5_GIB", "DETECTOR_FAILED_JOBS", "LAST_MORNING_PARTIAL",
                     "MORNING_TIMER_ENABLED_BEFORE_FIRST_VERIFIED_VIDEO", "CLOCK_NOT_SYNCHRONIZED",
                     "USER_LINGER_DISABLED"}
            if any(not isinstance(item, Mapping) or item.get("code") not in known and
                   not (isinstance(item.get("code"), str) and
                        (item["code"].startswith("DATABASE_") or item["code"].startswith("MORNING_")))
                   for item in raw_issues):
                found.add("STATUS_ISSUES")
            if any(isinstance(item, Mapping) and item.get("code") in
                   {"SSD_FREE_UNDER_5_GIB", "USER_LINGER_DISABLED"} for item in raw_issues):
                found.add("STATUS_ISSUES")
        # Never treat unrecognized status categories as positive confirmation.
        if snapshot.get("status") not in {"OK", "WARNING", "CRITICAL", "SETUP_WAITING_FOR_PHONES"}:
            found.add("EVIDENCE_INVALID")
        elif snapshot.get("status") in {"WARNING", "CRITICAL"} and not found:
            found.add("STATUS_ISSUES")
    # Missing mount suppresses derivative DB/freespace issues, not independent timer issues.
    if "STORAGE_UNVERIFIED" in found:
        found.difference_update({"LOW_SPACE", "DB_READ_ERROR", "FAILED_JOBS", "PARTIAL_NIGHT"})
    ordered = [code for code in PRIORITY if code in found]
    items = [{"code": code, "level": CAUSES[code][0], "observation": CAUSES[code][1],
              "recommendation": CAUSES[code][2]} for code in ordered]
    if "EVIDENCE_INVALID" in found or "EVIDENCE_STALE" in found:
        level = "UNVERIFIED"
    elif any(item["level"] == "CRITICAL" for item in items):
        level = "CRITICAL"
    elif items:
        level = "WARNING"
    else:
        level = "OBSERVED_OK" if isinstance(snapshot, Mapping) and snapshot.get("status") == "OK" else "INCOMPLETE"
    # Incident key is independent of timestamps (dedup); issues can recur over days.
    fingerprint = hashlib.sha256(json.dumps({"codes": ordered, "level": level},
                                           sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]
    should_alert = bool(ordered) and fingerprint != previous_fingerprint
    return {
        "schema": AGENT_SCHEMA, "mode": "observe", "evaluated_utc": now.isoformat(),
        "evidence_checked_utc": checked.isoformat() if checked else None,
        "evidence_age_seconds": int(age) if age is not None else None,
        "evidence_status": "OBSERVED" if level not in ("UNVERIFIED",) else "UNVERIFIED",
        "level": level, "causes": items,
        "summary": "Yeni gece tamamlandı iddiası yok; yalnız altyapı durum değerlendirmesi.",
        "incident_fingerprint": fingerprint,
        "alert": {"suggest_emit": should_alert, "duplicate_of_previous": bool(ordered) and not should_alert,
                  "delivered": False},
        "operator_review_required": bool(ordered),
        "mutation_enabled": False, "auto_retry_enabled": False,
        "morning_timer_enable_allowed": False,
        "deletion_enabled": False, "actions_executed": [],
    }
