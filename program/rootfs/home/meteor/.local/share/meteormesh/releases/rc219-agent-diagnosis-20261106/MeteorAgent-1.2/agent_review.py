"""MeteorAgent AutoPilot 1.2: deterministic read-only handoff to the operator.

Only interprets the verified v1 status snapshot and the v1.0 agent decision.
It does not run, enqueue, retry, contact phones, or write local files.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


# The status source reports COUNTS, not individual jobs or session completeness.
# No write operation can ever be inferred to be safe from it.

def _count(collection: Any, state: str) -> int:
    if not isinstance(collection, Mapping):
        return 0
    value = collection.get(state)
    return value if type(value) is int and value >= 0 else 0


def make_review(snapshot: Any, decision: dict[str, Any]) -> dict[str, Any]:
    """Read-only summary; MM-01 has no production integration acceptance yet."""
    causes = {c["code"] for c in decision["causes"]}
    if not isinstance(snapshot, Mapping):
        snapshot = {}
    database = snapshot.get("database")
    database = database if isinstance(database, Mapping) else {}
    analysis = snapshot.get("analysis_scope")
    analysis = analysis if isinstance(analysis, Mapping) else {}
    scoped = analysis.get("trusted_current_night") is True
    jobs = analysis.get("current_night_queue") if scoped else {}
    jobs = jobs if isinstance(jobs, Mapping) else {}
    failed = _count(jobs, "FAILED")
    waiting = _count(jobs, "WAITING") + _count(jobs, "PENDING")
    running = _count(jobs, "RUNNING")
    global_jobs = analysis.get("global_queue_observed")
    if not isinstance(global_jobs, Mapping):
        global_jobs = database.get("jobs")
    global_jobs = global_jobs if isinstance(global_jobs, Mapping) else {}
    source_missing = _count(analysis, "waiting_source_missing") if scoped else 0
    historical = analysis.get("historical_queue") if scoped else {}
    historical = historical if isinstance(historical, Mapping) else {}

    if "EVIDENCE_INVALID" in causes or "EVIDENCE_STALE" in causes:
        state, reason, step = (
            "UNKNOWN", "EVIDENCE_UNTRUSTED",
            "Güncel, salt okunur durum kanıtı alınmadan işlem planlama.")
    elif "STORAGE_UNVERIFIED" in causes:
        state, reason, step = (
            "BLOCKED", "STORAGE_NOT_VERIFIED",
            "Disk bağlantısı ve UUID sorununu operatör kontrol etmeli; yazma başlatma.")
    elif "DB_READ_ERROR" in causes:
        state, reason, step = (
            "BLOCKED", "DATABASE_UNAVAILABLE",
            "Veritabanı okunamadı; otomatik yeniden deneme yapılmamalı.")
    elif failed:
        state, reason, step = (
            "REVIEW", "FAILED_JOBS_PRESENT",
            "Başarısız işlerin nedenini incele; otomatik retry yok.")
    elif not scoped and _count(global_jobs, "FAILED"):
        state, reason, step = (
            "REVIEW", "FAILED_JOBS_SCOPE_UNKNOWN",
            "Global FAILED sayısı mevcut; güncel gece raporu eşleşene kadar bunu tarihsel/kapsamı belirsiz göster, geceye atfetme ve otomatik retry yapma.")
    elif not scoped and any(_count(global_jobs, key) for key in ("WAITING", "PENDING", "RUNNING")):
        state, reason, step = (
            "OBSERVE", "QUEUE_SCOPE_UNKNOWN",
            "Global kuyruk sayıları var; gece kapsamı raporu gelene kadar bunları güncel geceye atfetme.")
    elif source_missing:
        state, reason, step = (
            "REVIEW", "SOURCE_MISSING_REVIEW_REQUIRED",
            "Kaynağı eksik işleri inceleme durumunda tut; otomatik retry veya yeniden oluşturma yok.")
    elif waiting or running:
        state, reason, step = (
            "OBSERVE", "HUB_JOBS_PRESENT",
            "Mevcut iş kuyruğunu izle; yeni iş oluşturma veya ikinci worker açma.")
    else:
        state, reason, step = (
            "OBSERVE", "NO_ACTION_REQUIRED",
            "Yeni gece tamamlandı anlamına gelmez; yalnız sistem durumunu izlemeye devam et.")

    return {
        "schema": "meteormesh.agent-review.v1",
        "state": state,
        "reason": reason,
        "operator_next_step": step,
        "hub_queue_counts": {"failed": failed, "waiting": waiting, "running": running},
        "global_queue_observed": {
            "failed": _count(global_jobs, "FAILED"),
            "waiting": _count(global_jobs, "WAITING") + _count(global_jobs, "PENDING"),
            "running": _count(global_jobs, "RUNNING"),
        },
        "queue_scope": "CURRENT_NIGHT" if scoped else "GLOBAL_FALLBACK_SCOPE_UNKNOWN",
        "waiting_source_missing": source_missing,
        "historical_queue_counts": {
            "failed": _count(historical, "FAILED"),
            "waiting": _count(historical, "WAITING") + _count(historical, "PENDING"),
            "running": _count(historical, "RUNNING"),
        },
        "mm01_production_gate": "OPEN_NOT_INTEGRATED",
        "scheduled_morning_ingest": "RC14_CONFIGURED",
        "agent_permission": "OBSERVE_ONLY",
        "can_auto_retry": False,
        "can_enqueue": False,
        "actions_executed": [],
    }
