-- =====================================================================
-- Hospital Patient Services Assistant — database schema (Phase 1)
-- Each block below stands in for one real hospital system.
-- ALL DATA IN THIS PROJECT IS SYNTHETIC.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS vector;   -- used from Phase 2 (embeddings)

-- ---------------------------------------------------------------------
-- 1. EMR core: patients and doctors
-- ---------------------------------------------------------------------
CREATE TABLE patients (
    patient_id          TEXT PRIMARY KEY,                 -- e.g. P1001
    full_name           TEXT NOT NULL,
    dob                 DATE NOT NULL,
    gender              TEXT CHECK (gender IN ('M','F','O')),
    phone               TEXT,                             -- sensitive → mask
    email               TEXT,                             -- sensitive → mask
    national_id         TEXT,                             -- sensitive → mask
    blood_group         TEXT,
    allergies           TEXT[] NOT NULL DEFAULT '{}',
    chronic_conditions  TEXT[] NOT NULL DEFAULT '{}',     -- e.g. {diabetes}
    created_at          TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE doctors (
    doctor_id       TEXT PRIMARY KEY,                     -- e.g. D201
    full_name       TEXT NOT NULL,
    department      TEXT NOT NULL,
    specialization  TEXT,
    is_surgeon      BOOLEAN NOT NULL DEFAULT false
);

-- ---------------------------------------------------------------------
-- 2. Identity & access (who is logged in, what may they see)
-- ---------------------------------------------------------------------
CREATE TABLE users (
    user_id       TEXT PRIMARY KEY,
    auth_subject  TEXT UNIQUE NOT NULL,                   -- login name / token "sub"
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('patient','staff','doc_admin','sys_admin')),
    patient_id    TEXT REFERENCES patients(patient_id),   -- only for role = patient
    department    TEXT,                                   -- only for role = staff
    is_active     BOOLEAN NOT NULL DEFAULT true,
    -- a patient user MUST be linked to a patient; nobody else may be
    CHECK ((role = 'patient') = (patient_id IS NOT NULL))
);

-- Staff may only see patients they are assigned to ("assigned responsibilities")
CREATE TABLE care_assignments (
    staff_user_id  TEXT NOT NULL REFERENCES users(user_id),
    patient_id     TEXT NOT NULL REFERENCES patients(patient_id),
    assigned_at    TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (staff_user_id, patient_id)
);

-- ---------------------------------------------------------------------
-- 3. Procedure catalogue (shared by orders, insurance, billing)
-- ---------------------------------------------------------------------
CREATE TABLE procedures (
    procedure_code  TEXT PRIMARY KEY,                     -- e.g. TKR
    name            TEXT NOT NULL,
    department      TEXT NOT NULL,
    category        TEXT NOT NULL CHECK (category IN ('surgery','imaging','lab','consultation','diagnostic','cosmetic')),
    base_cost       NUMERIC(12,2) NOT NULL                -- INR
);

-- ---------------------------------------------------------------------
-- 4. Appointment Management System
-- ---------------------------------------------------------------------
CREATE TABLE appointment_slots (
    slot_id       BIGSERIAL PRIMARY KEY,
    doctor_id     TEXT NOT NULL REFERENCES doctors(doctor_id),
    start_time    TIMESTAMP NOT NULL,
    duration_min  INT NOT NULL DEFAULT 30,
    slot_type     TEXT NOT NULL DEFAULT 'consultation' CHECK (slot_type IN ('consultation','imaging','operating_theatre')),
    is_available  BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (doctor_id, start_time)
);

CREATE TABLE appointments (
    appointment_id    TEXT PRIMARY KEY,                   -- e.g. A5001
    patient_id        TEXT NOT NULL REFERENCES patients(patient_id),
    doctor_id         TEXT NOT NULL REFERENCES doctors(doctor_id),
    slot_id           BIGINT NOT NULL REFERENCES appointment_slots(slot_id),
    appointment_type  TEXT NOT NULL CHECK (appointment_type IN ('consultation','follow_up','imaging','surgery','pre_op_review')),
    department        TEXT NOT NULL,
    reason            TEXT,
    status            TEXT NOT NULL CHECK (status IN ('scheduled','completed','cancelled','no_show')),
    created_at        TIMESTAMP NOT NULL DEFAULT now(),
    updated_at        TIMESTAMP NOT NULL DEFAULT now()
);
-- one active booking per slot
CREATE UNIQUE INDEX one_active_booking_per_slot ON appointments(slot_id) WHERE status = 'scheduled';

-- ---------------------------------------------------------------------
-- 5. EMR orders + pre-operative protocol (structured) + readiness checklist
-- ---------------------------------------------------------------------
CREATE TABLE medical_orders (
    order_id                TEXT PRIMARY KEY,             -- e.g. O7001
    patient_id              TEXT NOT NULL REFERENCES patients(patient_id),
    ordered_by              TEXT NOT NULL REFERENCES doctors(doctor_id),
    order_type              TEXT NOT NULL CHECK (order_type IN ('lab','radiology','diagnostic','consult','procedure')),
    item_code               TEXT NOT NULL,                -- e.g. CBC, CXR, TKR
    item_name               TEXT NOT NULL,
    related_procedure_code  TEXT REFERENCES procedures(procedure_code),  -- e.g. pre-op test for TKR
    status                  TEXT NOT NULL CHECK (status IN ('ordered','in_progress','completed','cancelled')),
    ordered_at              TIMESTAMP NOT NULL,
    completed_at            TIMESTAMP
);

-- Structured copy of the approved Pre-operative Assessment Protocol (DOC003).
-- The document is cited to patients; this table is what the deterministic tool uses.
CREATE TABLE preop_requirements (
    procedure_code  TEXT NOT NULL REFERENCES procedures(procedure_code),
    item_code       TEXT NOT NULL,
    item_name       TEXT NOT NULL,
    validity_days   INT NOT NULL,                         -- result must be this recent
    applies_to      TEXT NOT NULL DEFAULT 'all',          -- 'all' or a chronic condition, e.g. 'diabetes'
    rule            TEXT,                                 -- extra rule, e.g. 'HbA1c must be below 8.0'
    source_document TEXT NOT NULL,                        -- e.g. DOC003 §4.1
    PRIMARY KEY (procedure_code, item_code)
);

CREATE TABLE procedure_readiness (
    patient_id      TEXT NOT NULL REFERENCES patients(patient_id),
    procedure_code  TEXT NOT NULL REFERENCES procedures(procedure_code),
    item_code       TEXT NOT NULL,
    item_name       TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('done','pending','not_applicable')),
    updated_at      TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (patient_id, procedure_code, item_code)
);

-- ---------------------------------------------------------------------
-- 6. Laboratory Information System (LIS)
-- ---------------------------------------------------------------------
CREATE TABLE lab_results (
    result_id        TEXT PRIMARY KEY,                    -- e.g. L3001
    patient_id       TEXT NOT NULL REFERENCES patients(patient_id),
    order_id         TEXT REFERENCES medical_orders(order_id),
    test_code        TEXT NOT NULL,                       -- e.g. HBA1C, FBS
    test_name        TEXT NOT NULL,
    value            NUMERIC(10,2) NOT NULL,
    unit             TEXT NOT NULL,
    ref_low          NUMERIC(10,2),
    ref_high         NUMERIC(10,2),
    flag             TEXT NOT NULL CHECK (flag IN ('normal','low','high','critical')),
    status           TEXT NOT NULL DEFAULT 'final' CHECK (status IN ('preliminary','final')),
    collected_at     TIMESTAMP NOT NULL,
    reported_at      TIMESTAMP NOT NULL
);
CREATE INDEX lab_results_patient_test ON lab_results(patient_id, test_code, collected_at DESC);

-- ---------------------------------------------------------------------
-- 7. Radiology Information System (RIS)
-- ---------------------------------------------------------------------
CREATE TABLE radiology_reports (
    report_id       TEXT PRIMARY KEY,                     -- e.g. R4001
    patient_id      TEXT NOT NULL REFERENCES patients(patient_id),
    order_id        TEXT REFERENCES medical_orders(order_id),
    modality        TEXT NOT NULL,                        -- X-ray, MRI, CT, Ultrasound
    body_part       TEXT NOT NULL,
    findings        TEXT NOT NULL,
    impression      TEXT NOT NULL,
    radiologist_id  TEXT REFERENCES doctors(doctor_id),
    status          TEXT NOT NULL DEFAULT 'final' CHECK (status IN ('preliminary','final')),
    performed_at    TIMESTAMP NOT NULL
);

-- ---------------------------------------------------------------------
-- 8. Insurance (plans, patient policies, coverage rules, pre-authorisations)
-- ---------------------------------------------------------------------
CREATE TABLE insurance_plans (
    plan_id       TEXT PRIMARY KEY,
    provider      TEXT NOT NULL,
    plan_name     TEXT NOT NULL,
    annual_limit  NUMERIC(12,2) NOT NULL
);

CREATE TABLE insurance_policies (
    policy_id      TEXT PRIMARY KEY,                      -- e.g. POL9001
    patient_id     TEXT NOT NULL REFERENCES patients(patient_id),
    plan_id        TEXT NOT NULL REFERENCES insurance_plans(plan_id),
    policy_number  TEXT NOT NULL,                         -- sensitive → mask
    member_since   DATE NOT NULL,                         -- used for waiting periods
    valid_from     DATE NOT NULL,
    valid_to       DATE NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('active','expired','suspended'))
);

CREATE TABLE coverage_rules (
    rule_id              TEXT PRIMARY KEY,
    plan_id              TEXT NOT NULL REFERENCES insurance_plans(plan_id),
    procedure_code       TEXT NOT NULL REFERENCES procedures(procedure_code),
    coverage_status      TEXT NOT NULL CHECK (coverage_status IN ('covered','partial','not_covered')),
    coverage_percent     INT NOT NULL CHECK (coverage_percent BETWEEN 0 AND 100),
    max_amount           NUMERIC(12,2),
    requires_preauth     BOOLEAN NOT NULL DEFAULT false,
    waiting_period_days  INT NOT NULL DEFAULT 0,
    conditions           JSONB NOT NULL DEFAULT '{}',     -- extra conditions, e.g. required diagnosis
    effective_from       DATE NOT NULL,
    effective_to         DATE,                            -- NULL = open-ended
    source_document      TEXT NOT NULL                    -- which insurance agreement this came from
);

CREATE TABLE prior_authorizations (
    auth_id         TEXT PRIMARY KEY,                     -- e.g. PA6001
    patient_id      TEXT NOT NULL REFERENCES patients(patient_id),
    policy_id       TEXT NOT NULL REFERENCES insurance_policies(policy_id),
    procedure_code  TEXT NOT NULL REFERENCES procedures(procedure_code),
    status          TEXT NOT NULL CHECK (status IN ('pending','approved','denied')),
    requested_at    TIMESTAMP NOT NULL,
    decided_at      TIMESTAMP,
    valid_until     DATE
);

-- ---------------------------------------------------------------------
-- 9. Billing System
-- ---------------------------------------------------------------------
CREATE TABLE bills (
    bill_id            TEXT PRIMARY KEY,                  -- e.g. B8001
    patient_id         TEXT NOT NULL REFERENCES patients(patient_id),
    description        TEXT NOT NULL,
    total_amount       NUMERIC(12,2) NOT NULL,
    insurance_covered  NUMERIC(12,2) NOT NULL DEFAULT 0,
    patient_payable    NUMERIC(12,2) NOT NULL,
    amount_paid        NUMERIC(12,2) NOT NULL DEFAULT 0,
    status             TEXT NOT NULL CHECK (status IN ('unpaid','partially_paid','paid')),
    issued_at          DATE NOT NULL,
    due_date           DATE NOT NULL,
    CHECK (total_amount = insurance_covered + patient_payable)
);

CREATE TABLE bill_items (
    item_id         BIGSERIAL PRIMARY KEY,
    bill_id         TEXT NOT NULL REFERENCES bills(bill_id),
    procedure_code  TEXT REFERENCES procedures(procedure_code),
    description     TEXT NOT NULL,
    amount          NUMERIC(12,2) NOT NULL
);

-- ---------------------------------------------------------------------
-- 10. Pharmacy System
-- ---------------------------------------------------------------------
CREATE TABLE medications (
    drug_code     TEXT PRIMARY KEY,                       -- e.g. METFORMIN
    generic_name  TEXT NOT NULL,
    brand_name    TEXT,
    drug_class    TEXT NOT NULL
);

CREATE TABLE prescriptions (
    prescription_id  TEXT PRIMARY KEY,                    -- e.g. RX2001
    patient_id       TEXT NOT NULL REFERENCES patients(patient_id),
    doctor_id        TEXT NOT NULL REFERENCES doctors(doctor_id),
    drug_code        TEXT NOT NULL REFERENCES medications(drug_code),
    dose             TEXT NOT NULL,
    frequency        TEXT NOT NULL,
    start_date       DATE NOT NULL,
    end_date         DATE,
    status           TEXT NOT NULL CHECK (status IN ('active','completed','stopped'))
);

-- "Approved medication knowledge source" for drug_interaction_check.
-- Each pair stored once, alphabetical order (drug_a < drug_b).
CREATE TABLE drug_interactions (
    drug_a       TEXT NOT NULL REFERENCES medications(drug_code),
    drug_b       TEXT NOT NULL REFERENCES medications(drug_code),
    severity     TEXT NOT NULL CHECK (severity IN ('minor','moderate','major','contraindicated')),
    description  TEXT NOT NULL,
    source       TEXT NOT NULL,
    PRIMARY KEY (drug_a, drug_b),
    CHECK (drug_a < drug_b)
);

-- ---------------------------------------------------------------------
-- 11. Knowledge-base registry (rows are added by the Phase 2 ingestion pipeline)
-- ---------------------------------------------------------------------
CREATE TABLE documents (
    document_id      TEXT PRIMARY KEY,                    -- e.g. DOC001
    document_name    TEXT NOT NULL,                       -- file name
    title            TEXT NOT NULL,
    department       TEXT NOT NULL,
    document_type    TEXT NOT NULL CHECK (document_type IN (
                        'hospital_sop','clinical_protocol','nursing_procedure','admission_policy',
                        'discharge_guideline','insurance_agreement','emergency_procedure','government_advisory')),
    lineage_id       TEXT NOT NULL,                       -- same for all versions of one policy
    version          TEXT NOT NULL,
    effective_date   DATE NOT NULL,
    expiry_date      DATE,
    approval_status  TEXT NOT NULL CHECK (approval_status IN ('draft','approved','superseded','retired')),
    authority_level  INT NOT NULL CHECK (authority_level BETWEEN 1 AND 4),  -- 1 = highest (regulator)
    uploaded_by      TEXT NOT NULL,
    uploaded_at      TIMESTAMP NOT NULL DEFAULT now(),
    file_path        TEXT NOT NULL,
    checksum         TEXT                                 -- detects re-uploads of the same file
);

-- ---------------------------------------------------------------------
-- 12. Audit log (R10) — one row per request
-- ---------------------------------------------------------------------
CREATE TABLE audit_logs (
    trace_id           TEXT PRIMARY KEY,                  -- e.g. HSP-93214
    user_id            TEXT REFERENCES users(user_id),
    role               TEXT,
    action             TEXT NOT NULL,                     -- chat_query, reschedule, document_upload ...
    query_redacted     TEXT,                              -- user text with sensitive values masked
    intent             TEXT,
    answer_type        TEXT,
    patient_accessed   TEXT,
    retrieved_sources  JSONB NOT NULL DEFAULT '[]',
    tools_invoked      JSONB NOT NULL DEFAULT '[]',
    privacy_checks     JSONB NOT NULL DEFAULT '[]',
    model_name         TEXT,
    latency_ms         INT,
    created_at         TIMESTAMP NOT NULL DEFAULT now()
);
