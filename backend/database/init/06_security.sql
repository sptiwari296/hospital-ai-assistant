-- =====================================================================
-- Phase 7: database-level security
--   * hospital_api: a least-privilege login role for the API (no DDL, no superuser)
--   * Row-Level Security: on patient tables the API role sees ONLY the rows of the
--     patient set in the session variable app.patient_id (default: nothing at all)
--   * audit_logs is append-only for the API role (INSERT + SELECT, no UPDATE/DELETE)
-- The role's password is set by scripts/setup_security.py (from API_DB_PASSWORD in .env).
-- Apply on an existing database:
--   Get-Content backend\database\init\06_security.sql -Raw | docker exec -i hospital_db psql -U hospital_app -d hospital
-- =====================================================================

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hospital_api') THEN
        CREATE ROLE hospital_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END $$;

-- Columns for login lockout and encrypted identity numbers
ALTER TABLE users    ADD COLUMN IF NOT EXISTS failed_login_count INT NOT NULL DEFAULT 0;
ALTER TABLE users    ADD COLUMN IF NOT EXISTS locked_until TIMESTAMP;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS national_id_encrypted TEXT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS authorization_status TEXT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS endpoint TEXT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS http_status INT;

-- ---------------------------------------------------------------- privileges (least privilege)
GRANT USAGE ON SCHEMA public TO hospital_api;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO hospital_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO hospital_api;
-- writes the application genuinely performs
GRANT UPDATE (slot_id, doctor_id, status, updated_at) ON appointments TO hospital_api;
GRANT UPDATE (is_available) ON appointment_slots TO hospital_api;
GRANT INSERT, UPDATE ON pending_actions, chat_sessions, ingestion_jobs, documents TO hospital_api;
GRANT INSERT, DELETE ON document_chunks TO hospital_api;
GRANT UPDATE (failed_login_count, locked_until) ON users TO hospital_api;
GRANT INSERT ON audit_logs TO hospital_api;       -- append-only: no UPDATE, no DELETE

-- ---------------------------------------------------------------- Row-Level Security
-- current_setting(..., true) returns NULL when unset, and NULL = x is never true -> deny by default.
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['patients', 'appointments', 'medical_orders', 'procedure_readiness', 'lab_results',
                             'radiology_reports', 'insurance_policies', 'prior_authorizations', 'bills',
                             'prescriptions', 'pending_actions']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS patient_isolation ON %I', t);
        EXECUTE format($p$CREATE POLICY patient_isolation ON %I TO hospital_api
                          USING (patient_id = current_setting('app.patient_id', true))
                          WITH CHECK (patient_id = current_setting('app.patient_id', true))$p$, t);
    END LOOP;
END $$;

-- Staff need the NAMES of their own assigned patients (to pick one) before any patient scope exists.
-- A view runs with its owner's rights, so this is a deliberate, narrow exception to RLS:
-- only id + name, only for the staff user set in app.user_id.
CREATE OR REPLACE VIEW my_assigned_patients AS
SELECT p.patient_id, p.full_name
FROM care_assignments c JOIN patients p USING (patient_id)
WHERE c.staff_user_id = current_setting('app.user_id', true);
GRANT SELECT ON my_assigned_patients TO hospital_api;

-- bill_items has no patient_id: visible only through a visible bill
ALTER TABLE bill_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS patient_isolation ON bill_items;
CREATE POLICY patient_isolation ON bill_items TO hospital_api
    USING (EXISTS (SELECT 1 FROM bills b WHERE b.bill_id = bill_items.bill_id));
