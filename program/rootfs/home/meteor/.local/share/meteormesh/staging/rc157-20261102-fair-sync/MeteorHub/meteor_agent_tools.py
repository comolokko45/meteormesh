"""Strictly allowlisted, read-only agent tools; never execute model-generated code."""

from __future__ import annotations

from pathlib import Path

from meteor_agent import answer, read_only_connection
from meteor_knowledge import Knowledge

TOOL_MAP = {
    "station_status": ("MeteorHub kayıt, analiz ve başarısızlık durumlarını göster.", None, "status"),
    "camera_summary": ("Kamera başına doğrulanmış video sayısını ve boyutunu göster.", None, "cameras"),
    "job_queue": ("Bekleyen, çalışan ve başarısız analiz işlerini listele.", None, "pending"),
    "retention_status": ("Ham videoların neden silinemeyeceğini açıkla.", None, "retention"),
    "daily_briefing": ("Günlük istasyon durumunu Türkçe özetle.", None, "briefing"),
    "morning_report": ("Son otomatik sabah indirme ve analiz çalışmasını özetle.", None, None),
    "recording_health": ("Kayıt süresi, doğrulama ve henüz ölçülmemiş kare boşluklarını göster.", None, None),
    "knowledge_search": ("Onaylı meteor, kamera ve MeteorMesh bilgi notlarında arama yap.",
                         "query", None),
}


def tool_schemas() -> list[dict]:
    result = []
    for name, (description, argument, _) in TOOL_MAP.items():
        params = {"type": "object", "properties": {}, "additionalProperties": False}
        if argument is not None:
            params["properties"][argument] = {"type": "string", "description": "Aranacak kavram"}
            params["required"] = [argument]
        result.append({"type": "function", "function": {
            "name": name, "description": description, "parameters": params}})
    return result


class ReadOnlyTools:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.db_path = data_dir / "meteorhub.sqlite3"
        self.knowledge = Knowledge()

    def execute(self, name: str, arguments: dict | None = None) -> dict:
        if name not in TOOL_MAP:
            raise ValueError("Tool not allowlisted")
        arguments = {} if arguments is None else arguments
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
        _, parameter, question = TOOL_MAP[name]
        if set(arguments) != ({parameter} if parameter else set()):
            raise ValueError("Unknown or missing tool argument")
        if parameter:
            return {"sources": self.knowledge.search(arguments[parameter])}
        if name == "recording_health":
            from meteor_health import summarize_health
            # Only configured station cameras; no arbitrary SQL, writes or paths.
            cameras = ["cam-ruby", "cam-viva", "cam-surya"]
            with read_only_connection(self.db_path) as conn:
                return summarize_health(conn, cameras)
        if name == "morning_report":
            from meteor_morning import last_run
            try:
                report = last_run(self.data_dir)
            except FileNotFoundError:
                return {"status": "NO_MORNING_RUN_YET", "auto_delete": False}
            if not isinstance(report, dict):
                raise ValueError("Morning report must be an object")
            allowed_ids = {"cam-ruby", "cam-viva", "cam-surya"}
            allowed_states = {"OK", "INCOMPLETE", "NO_READY_SEGMENTS",
                              "OFFLINE_OR_ERROR", "SKIPPED_TIME_BUDGET"}
            devices = report.get("devices", [])
            if not isinstance(devices, list):
                raise ValueError("Morning camera records must be a list")
            safe_cameras = []
            for item in devices[:10]:
                if not isinstance(item, dict):
                    continue
                device_id = item.get("id")
                if type(device_id) is not str or device_id not in allowed_ids:
                    continue
                state = item.get("state")
                safe_cameras.append({"id": device_id,
                                     "state": state if type(state) is str and
                                     state in allowed_states else "UNKNOWN"})
            errors = report.get("errors", [])
            if not isinstance(errors, list):
                errors = []
            status = report.get("outcome")
            if status not in ("COMPLETED_PROVISIONAL", "PARTIAL_REVIEW_REQUIRED"):
                status = "UNKNOWN"
            def safe_count(field: str) -> int:
                count = report.get(field)
                return count if type(count) is int and 0 <= count <= 1_000_000 else 0
            return {
                "status": status,
                "cameras": safe_cameras,
                "segments_received": safe_count("sync_good"),
                "jobs_completed": safe_count("jobs_completed_this_run"),
                "error_count": min(len(errors), 1_000_000),
                "auto_delete": False,
                "scientific_confirmation": False,
            }
        with read_only_connection(self.db_path) as conn:
            result = answer(conn, question)
        if name == "job_queue":
            result["jobs"] = [{**job, "error": str(job.get("error") or "")[:200]}
                              for job in result["jobs"][:10]]
            result["limited_to"] = 10
        return result
