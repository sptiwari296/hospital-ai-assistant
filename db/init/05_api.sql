-- =====================================================================
-- Phase 6: API support — login, persisted chat sessions, ingestion jobs
-- On an existing database apply with:
--   Get-Content db\init\05_api.sql -Raw | docker exec -i hospital_db psql -U hospital_app -d hospital
-- =====================================================================

-- Salted PBKDF2 hash ("pbkdf2_sha256$iterations$salt$hash"); plain passwords are never stored.
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;

-- Conversation state between HTTP requests (history, offered slots, pending confirmation).
-- Identity is NOT stored here: it is rebuilt from the login token on every request.
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id          TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES users(user_id),
    acting_for_patient  TEXT REFERENCES patients(patient_id),   -- staff: selected assigned patient
    state               JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMP NOT NULL DEFAULT now(),
    updated_at          TIMESTAMP NOT NULL DEFAULT now()
);

-- Live document ingestion (R11): one row per upload, updated by the background worker.
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id        TEXT PRIMARY KEY,                -- e.g. JOB-1A2B3C4D
    document_id   TEXT NOT NULL,
    file_name     TEXT NOT NULL,
    uploaded_by   TEXT NOT NULL REFERENCES users(user_id),
    status        TEXT NOT NULL CHECK (status IN ('queued', 'running', 'ingested', 'unchanged', 'duplicate',
                                                  'rejected', 'failed')),
    message       TEXT,
    trace_id      TEXT,                            -- ingestion audit trace (ING-...)
    chunk_count   INT,
    created_at    TIMESTAMP NOT NULL DEFAULT now(),
    finished_at   TIMESTAMP
);
