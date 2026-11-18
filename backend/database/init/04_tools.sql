-- =====================================================================
-- Phase 4: operational tools
-- On an existing database apply with:
--   Get-Content backend\database\init\04_tools.sql -Raw | docker exec -i hospital_db psql -U hospital_app -d hospital
-- =====================================================================

-- Network restriction: is this hospital in the insurer's network for the plan?
ALTER TABLE insurance_plans ADD COLUMN IF NOT EXISTS hospital_in_network BOOLEAN NOT NULL DEFAULT true;

-- Two-step write actions: a tool PROPOSES a change (row = 'pending'); it is executed
-- only when the same user confirms this confirmation_id before it expires.
-- The table doubles as a permanent log of every requested and executed change.
CREATE TABLE IF NOT EXISTS pending_actions (
    confirmation_id  TEXT PRIMARY KEY,                       -- e.g. CNF-3F9A1C22B0
    trace_id         TEXT,
    user_id          TEXT NOT NULL REFERENCES users(user_id),
    patient_id       TEXT NOT NULL REFERENCES patients(patient_id),
    action           TEXT NOT NULL CHECK (action IN ('reschedule_appointment', 'cancel_appointment')),
    params           JSONB NOT NULL,
    summary          TEXT NOT NULL,                          -- what the user was asked to confirm
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'executed', 'failed', 'expired', 'declined')),
    created_at       TIMESTAMP NOT NULL,
    expires_at       TIMESTAMP NOT NULL,
    executed_at      TIMESTAMP,
    result           JSONB
);
CREATE INDEX IF NOT EXISTS pending_actions_user ON pending_actions(user_id, status);
