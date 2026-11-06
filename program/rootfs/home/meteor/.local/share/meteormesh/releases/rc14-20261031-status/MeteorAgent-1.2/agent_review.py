"""MeteorAgent 1.1: tiny, deterministic handoff to the human operator.

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
    jobs = database.get("jobs")
    jobs = jobs if isinstance(jobs, Mapping) else {}
    failed = _count(jobs, "FAILED")
    waiting = _count(jobs, "WAITING") + _count(jobs, "PENDING")
    running = _count(jobs, "RUNNING")

    if "EVIDENCE_INVALID" in causes or "EVIDENCE_STALE" in causes:
        state, reason, step = (
            "UNKNOWN", "EVIDENCE_UNTRUSTED",
            "Güncel, salt okunur durum kanıtı alınmadan işlem planlama.")
    elif "STORAGE_UNVERIFIED" in causes:
        state, reason, step = (
            "BLOCKED", "STORAGE_NOT_VERIFIED",
            "Disk bağlantısı ve UUID sorununu operatör kontrol etmeli; yazma başlatma.")
    elif "TIMER_STATE_UNKNOWN" in causes:
        state, reason, step = (
            "BLOCKED", "MORNING_TIMER_UNEXPECTED",
            "09:00 zamanlayıcısının etkin olduğunu operatör doğrulamalı.")
    elif "DB_READ_ERROR" in causes:
        state, reason, step = (
            "BLOCKED", "DATABASE_UNAVAILABLE",
            "Veritabanı okunamadı; otomatik yeniden deneme yapılmamalı.")
    elif failed:
        state, reason, step = (
            "REVIEW", "FAILED_JOBS_PRESENT",
            "Başarısız işlerin nedenini incele; otomatik retry yok.")
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
        "mm01_production_gate": "OPEN_NOT_INTEGRATED",
        "scheduled_morning_ingest": "RC14_CONFIGURED",
        "agent_permission": "OBSERVE_ONLY",
        "can_auto_retry": False,
        "can_enqueue": False,
        "actions_executed": [],
    }
