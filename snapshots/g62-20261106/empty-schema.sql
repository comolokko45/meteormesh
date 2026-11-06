CREATE TABLE segments (
        device_id TEXT NOT NULL, segment_id TEXT NOT NULL, sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL, state TEXT NOT NULL, ffprobe_json TEXT,
        verified_utc TEXT NOT NULL, PRIMARY KEY (device_id, segment_id)
    );
CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unique_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL CHECK(kind IN ('DETECT')),
            device_id TEXT NOT NULL,
            segment_id TEXT NOT NULL,
            video_sha256 TEXT NOT NULL,
            detector_version TEXT NOT NULL,
            detector_config_sha256 TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('WAITING','RUNNING','SUCCEEDED','FAILED')),
            attempts INTEGER NOT NULL DEFAULT 0,
            claimed_at REAL,
            last_error TEXT,
            created_utc TEXT NOT NULL,
            finished_utc TEXT
        );
CREATE TABLE detector_runs (
            job_id INTEGER NOT NULL UNIQUE,
            device_id TEXT NOT NULL,
            segment_id TEXT NOT NULL,
            input_sha256 TEXT NOT NULL,
            detector_version TEXT NOT NULL,
            detector_commit TEXT NOT NULL,
            config_sha256 TEXT NOT NULL,
            result_path TEXT NOT NULL,
            result_sha256 TEXT NOT NULL,
            duration_seconds REAL NOT NULL,
            completed_utc TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs(id)
        );
CREATE TABLE source_dispositions (
        device_id TEXT NOT NULL,
        segment_id TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        disposition TEXT NOT NULL CHECK(disposition IN (
            'DELETE_PENDING','NO_CANDIDATE_DELETED',
            'CANDIDATE_PRESERVED','ADJACENT_CANDIDATE_PRESERVED')),
        analysis_job_id INTEGER NOT NULL,
        result_sha256 TEXT NOT NULL,
        updated_utc TEXT NOT NULL,
        deleted_utc TEXT,
        PRIMARY KEY(device_id,segment_id)
    );
