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
    "LOW_SPACE": ("WARNING", "Planlanan büyük aktarım için HDD boş alanı düşük.", "Yeni büyük aktarımı planlama; saklama veya harici arşiv kararı operatöre ait."),
    "DB_READ_ERROR": ("WARNING", "MeteorHub SQLite durumu okunamadı.", "Disk doğrulanmışsa salt okunur DB/izin incelemesi yap; veritabanını sıfırlama."),
    "FAILED_JOBS": ("WARNING", "Güncel gece kapsamında FAILED analiz işleri bulunuyor.", "Güncel gece iş nedenlerini incele; otomatik yeniden deneme yapma."),
    "FAILED_JOBS_SCOPE_UNKNOWN": ("WARNING", "FAILED işleri var, ancak son raporun gece kapsamı güncel değil; bunları bu geceye atfetme.", "Geçerli gece raporu oluşana kadar sayıyı tarihsel/kapsamı belirsiz olarak göster; otomatik retry yapma."),
    "QUEUE_SCOPE_UNKNOWN": ("WARNING", "Global analiz kuyruğu var, ancak geçerli gece kapsamı raporu henüz yok.", "Global kuyruk sayılarını güncel geceye bağlama; kapsamlı rapor oluşana kadar yalnız izle."),
    "SOURCE_MISSING_JOBS": ("WARNING", "Güncel gece kapsamında kaynağı eksik bekleyen analiz işleri bulunuyor.", "Kaynağı yeniden oluşturulmuş sayma ve otomatik retry yapma; inceleme durumunda tut."),
    "PARTIAL_NIGHT": ("WARNING", "Son sabah raporu kısmi tamamlanma bildiriyor.", "Eksik kayıtları raporla; geceyi tamamlanmış sayma."),
    "CLOCK_UNSYNCED": ("WARNING", "Sunucu saat senkronizasyonu doğrulanmadı.", "UTC güvenilirliğini operatör incelemeli; saat ayarını otomatik değiştirme."),
    "MEMORY_LOW": ("WARNING", "Sunucunun kullanılabilir belleği düşük.", "Tek analiz işçisi politikasını koru; yeni iş ekleme."),
    "LOCAL_ALERTS_OPEN": ("WARNING", "Operasyon günlüğünde açık yerel uyarı var.", "Açık uyarıyı ve son gözlem zamanını incele; güvenli toparlamayı mevcut servisler yürütür."),
    "PHONE_STORAGE_LOW": ("WARNING", "Telefonun güvenli kayıt alanı eşiğine yaklaşıldı.", "Mevcut doğrulanmış aktarımın ilerleyişini izle; 32/33 GiB koruma eşiklerini düşürme."),
    "PHONE_API_DOWN": ("WARNING", "Telefon HTTP API bağlantısı doğrulanmadı.", "ADB durumundan bağımsız HTTP ve Control-013 kanıtını karşılaştır; otomatik kayıt başarısı iddia etme."),
    "CAPTURE_STALLED": ("WARNING", "Kayıt durumu görünse de tamamlanmış MP4 ilerlemiyor.", "READY/son MP4 zamanını doğrula; Android kamera yeniden hazırlama gerekiyorsa NEEDS_REARM bildir."),
    "PHONE_NEEDS_REARM": ("WARNING", "Telefon kamerası uzaktan hazır değil; kullanıcı yeniden hazırlamalı.", "Diğer cihazları çalışır bırak; Android izin sınırını aşan yeniden başlatma denemesi yapma."),
    "CONTROL_STALE": ("WARNING", "Control-013 heartbeat süresi aşıldı.", "HTTP API ve kayıt ilerlemesini ayrı kontrol et; yalnız heartbeat eskidi diye uygulamayı kapalı sayma."),
    "SYNC_FAILED": ("WARNING", "Doğrulanmış aktarım kalıcı hata bildiriyor.", "Mevcut .part/Range ve HDD güvenlik kapılarıyla sonraki sınırlı aktarımı izle; kaynak telefonu temizleme."),
    "ANALYSIS_FAILED": ("WARNING", "Analiz işlemi kalıcı hata bildiriyor.", "Güncel gece ve kaynak dosya kimliğini kontrol et; tarihsel FAILED işleri tekrar kuyruğa alma."),
    "LOCAL_ALERTS_UNAVAILABLE": ("WARNING", "Yerel operasyon alarm durumu okunamadı.", "OpsJournal durum dosyasını ve son status turunu incele."),
    "STATUS_ISSUES": ("WARNING", "Durum aracının açıklanmayan ek uyarıları mevcut.", "Orijinal durum raporunu incele; bilinmeyen uyarıyı otomatik düzeltme."),
    "EVIDENCE_STALE": ("WARNING", "Durum raporunun zamanı güncel veya güvenilir değil.", "Yeni salt okunur kanıt olmadan sistemin sağlıklı olduğunu varsayma."),
    "EVIDENCE_INVALID": ("CRITICAL", "Durum raporu geçerli şemaya veya güvenlik beyanına uymuyor.", "Kaynağı doğrula; hiçbir otomatik eylem başlatma."),
}
PRIORITY = ["EVIDENCE_INVALID", "STORAGE_UNVERIFIED", "EVIDENCE_STALE",
            "LOW_SPACE", "DB_READ_ERROR", "FAILED_JOBS", "FAILED_JOBS_SCOPE_UNKNOWN", "QUEUE_SCOPE_UNKNOWN", "SOURCE_MISSING_JOBS", "PARTIAL_NIGHT",
            "CLOCK_UNSYNCED", "MEMORY_LOW", "PHONE_STORAGE_LOW", "CAPTURE_STALLED", "PHONE_NEEDS_REARM", "PHONE_API_DOWN", "CONTROL_STALE", "SYNC_FAILED", "ANALYSIS_FAILED", "LOCAL_ALERTS_OPEN", "LOCAL_ALERTS_UNAVAILABLE", "STATUS_ISSUES"]

def classify_local_alerts(codes: list[str]) -> set[str]:
    """Explain existing OpsJournal incidents without turning them into commands."""
    found = set()
    for code in codes:
        if code in {"STORAGE_LOW:surya", "STORAGE_LOW:ruby", "STORAGE_LOW:viva"}:
            found.add("PHONE_STORAGE_LOW")
        elif code.startswith("CAPTURE_STALLED:") and code.split(":", 1)[1] in ("surya", "ruby", "viva"):
            found.add("CAPTURE_STALLED")
        elif code.startswith("CAMERA_NEEDS_REARM:") and code.split(":", 1)[1] in ("surya", "ruby", "viva"):
            found.add("PHONE_NEEDS_REARM")
        elif code.startswith("API_DOWN:") and code.split(":", 1)[1] in ("surya", "ruby", "viva"):
            found.add("PHONE_API_DOWN")
        elif code.startswith("CONTROL_HEARTBEAT_STALE:") and code.split(":", 1)[1] in ("surya", "ruby", "viva"):
            found.add("CONTROL_STALE")
        elif code == "SYNC_FAILED":found.add("SYNC_FAILED")
        elif code == "ANALYSIS_FAILED":found.add("ANALYSIS_FAILED")
    return found

def known_local_alert(code: str) -> bool:
    return (code in ("SYNC_FAILED", "ANALYSIS_FAILED") or
            (":" in code and code.split(":", 1)[0] in
             ("STORAGE_LOW", "CAPTURE_STALLED", "CAMERA_NEEDS_REARM", "API_DOWN", "CONTROL_HEARTBEAT_STALE")
             and code.split(":", 1)[1] in ("surya", "ruby", "viva")))


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
        # Daily analysis starts at 05:30; the separate 09:00 timer is optional.
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
            analysis = snapshot.get("analysis_scope")
            analysis = analysis if isinstance(analysis, Mapping) else {}
            if analysis.get("trusted_current_night") is True:
                current = analysis.get("current_night_queue")
                if _read_count(current, "FAILED"):
                    found.add("FAILED_JOBS")
                if _read_count(analysis, "waiting_source_missing"):
                    found.add("SOURCE_MISSING_JOBS")
            elif _read_count(db.get("jobs"), "FAILED"):
                # Global history is real evidence, but without a matching current
                # NightScope it must never be attributed to the active night.
                found.add("FAILED_JOBS_SCOPE_UNKNOWN")
                found.add("QUEUE_SCOPE_UNKNOWN")
            elif any(_read_count(db.get("jobs"), state) for state in ("WAITING", "PENDING", "RUNNING")):
                found.add("QUEUE_SCOPE_UNKNOWN")
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
        if "local_alerts" in snapshot:
            local_alerts = snapshot.get("local_alerts")
            if not isinstance(local_alerts, Mapping) or local_alerts.get("state") != "READ_OK":
                found.add("LOCAL_ALERTS_UNAVAILABLE")
            elif isinstance(local_alerts.get("active"), list) and local_alerts["active"]:
                active = local_alerts["active"]
                if not all(isinstance(code, str) and len(code) < 160 for code in active):
                    found.add("LOCAL_ALERTS_UNAVAILABLE")
                else:
                    specific = classify_local_alerts(active)
                    found.update(specific)
                    if not specific or any(not known_local_alert(code) for code in active):
                        found.add("LOCAL_ALERTS_OPEN")
            elif not isinstance(local_alerts.get("active"), list):
                found.add("LOCAL_ALERTS_UNAVAILABLE")
        raw_issues = snapshot.get("issues")
        if not isinstance(raw_issues, list) or len(raw_issues) > 200:
            found.add("EVIDENCE_INVALID")
        elif raw_issues:
            known = {"HDD_NOT_MOUNTED", "HDD_UNEXPECTED_FILESYSTEM", "HDD_UNEXPECTED_UUID",
                     "HDD_MOUNT_SOURCE_UNVERIFIED", "HDD_FREE_UNDER_5_GIB", "HDD_FREE_LOW",
                     "SSD_FREE_UNDER_5_GIB", "DETECTOR_FAILED_JOBS", "DETECTOR_CURRENT_NIGHT_FAILED_JOBS",
                     "DETECTOR_WAITING_SOURCE_MISSING", "DETECTOR_FAILED_JOBS_SCOPE_UNKNOWN",
                     "LAST_MORNING_PARTIAL",
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
        found.difference_update({"LOW_SPACE", "DB_READ_ERROR", "FAILED_JOBS", "FAILED_JOBS_SCOPE_UNKNOWN", "QUEUE_SCOPE_UNKNOWN", "SOURCE_MISSING_JOBS", "PARTIAL_NIGHT"})
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
