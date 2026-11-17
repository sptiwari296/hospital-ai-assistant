-- =====================================================================
-- Synthetic seed data — NO REAL PEOPLE. "Today" in the story = 2026-11-18.
--   P1001 Rahul Sharma : diabetic, knee replacement 26 Nov, 3 pre-op items pending, pre-auth pending
--   P1002 Priya Nair   : MRI brain 24 Nov, basic insurance (MRI partial, knee replacement not covered)
--   P1003 Arjun Mehta  : cardiac, warfarin + aspirin, insurance EXPIRED, partially paid bill
--   P1004 Sneha Kapoor : new insurance (Aug 2026) → still in waiting period for knee replacement
-- =====================================================================

-- ---------- Patients ----------
INSERT INTO patients (patient_id, full_name, dob, gender, phone, email, national_id, blood_group, allergies, chronic_conditions) VALUES
('P1001','Rahul Sharma','1968-03-14','M','+91-90000-10001','rahul.sharma@example.com','9000-1000-1001','B+','{penicillin}','{diabetes,hyperlipidemia,osteoarthritis_knee}'),
('P1002','Priya Nair',  '1990-07-22','F','+91-90000-10002','priya.nair@example.com',  '9000-1000-1002','O+','{}','{hypothyroidism}'),
('P1003','Arjun Mehta', '1955-11-02','M','+91-90000-10003','arjun.mehta@example.com', '9000-1000-1003','A-','{sulfa drugs}','{atrial_fibrillation,hypertension}'),
('P1004','Sneha Kapoor','1982-01-30','F','+91-90000-10004','sneha.kapoor@example.com','9000-1000-1004','AB+','{}','{osteoarthritis_knee}');

-- ---------- Doctors ----------
INSERT INTO doctors (doctor_id, full_name, department, specialization, is_surgeon) VALUES
('D201','Dr. Vikram Rao',   'Orthopedics',      'Joint replacement surgery', true),
('D202','Dr. Kavita Menon', 'Cardiology',       'Interventional cardiology', false),
('D203','Dr. Sameer Joshi', 'Radiology',        'MRI and CT imaging',        false),
('D204','Dr. Farah Khan',   'Endocrinology',    'Diabetes care',             false),
('D205','Dr. Rohan Das',    'Anesthesiology',   'Pre-anaesthesia assessment',false),
('D206','Dr. Neha Gupta',   'Cardiology',       'Preventive cardiology',     false),
('D207','Dr. Arvind Pillai','General Medicine', 'Internal medicine',         false);

-- ---------- Users (login identities) ----------
INSERT INTO users (user_id, auth_subject, display_name, role, patient_id, department) VALUES
('U001','rahul.sharma', 'Rahul Sharma', 'patient',  'P1001', NULL),
('U002','priya.nair',   'Priya Nair',   'patient',  'P1002', NULL),
('U003','arjun.mehta',  'Arjun Mehta',  'patient',  'P1003', NULL),
('U004','sneha.kapoor', 'Sneha Kapoor', 'patient',  'P1004', NULL),
('U101','nurse.anita',  'Anita (Nurse)','staff',    NULL, 'Orthopedics'),
('U102','nurse.deepak', 'Deepak (Nurse)','staff',   NULL, 'Cardiology'),
('U201','admin_01',     'Document Admin','doc_admin',NULL, NULL),
('U301','sysadmin_01',  'System Admin', 'sys_admin',NULL, NULL);

INSERT INTO care_assignments (staff_user_id, patient_id) VALUES
('U101','P1001'), ('U101','P1004'),   -- ortho nurse
('U102','P1003');                     -- cardiology nurse

-- ---------- Procedure catalogue (INR) ----------
INSERT INTO procedures (procedure_code, name, department, category, base_cost) VALUES
('TKR',            'Total Knee Replacement',          'Orthopedics',      'surgery',      300000),
('MRI_BRAIN',      'MRI Brain',                       'Radiology',        'imaging',       12000),
('MRI_KNEE',       'MRI Knee',                        'Radiology',        'imaging',       10000),
('XR_KNEE',        'X-ray Knee',                      'Radiology',        'imaging',         600),
('CXR',            'Chest X-ray (PA view)',           'Radiology',        'imaging',         600),
('ECG',            '12-lead ECG',                     'Cardiology',       'diagnostic',      500),
('CBC',            'Complete Blood Count',            'Laboratory',       'lab',             400),
('HBA1C',          'HbA1c (Glycated Haemoglobin)',    'Laboratory',       'lab',             600),
('PT_INR',         'Coagulation profile (PT/INR)',    'Laboratory',       'lab',             450),
('ORTHO_CONSULT',  'Orthopaedic consultation',        'Orthopedics',      'consultation',   1500),
('CARDIO_CONSULT', 'Cardiology consultation',         'Cardiology',       'consultation',   1500),
('GEN_CONSULT',    'General medicine consultation',   'General Medicine', 'consultation',    800),
('RHINOPLASTY',    'Cosmetic rhinoplasty',            'Plastic Surgery',  'cosmetic',     150000);

-- ---------- Appointment slots ----------
-- Weekday consultation/imaging slots 09:00–12:30 (every 30 min), Oct–Dec 2026, for every doctor
INSERT INTO appointment_slots (doctor_id, start_time, duration_min, slot_type)
SELECT d.doctor_id, day + t, 30,
       CASE WHEN d.department = 'Radiology' THEN 'imaging' ELSE 'consultation' END
FROM doctors d
CROSS JOIN generate_series('2026-10-01'::timestamp, '2026-12-31'::timestamp, interval '1 day') AS day
-- 8 slots per day: 09:00 + n × 30 min, n = 0..7  (generate_series can't step over intervals)
CROSS JOIN LATERAL (SELECT interval '9 hours' + n * interval '30 minutes' AS t FROM generate_series(0, 7) AS n) AS slot_times
WHERE extract(isodow FROM day) < 6;

-- Operating-theatre slots for the surgeon: Tue & Thu 14:00, 3 hours
INSERT INTO appointment_slots (doctor_id, start_time, duration_min, slot_type)
SELECT 'D201', day + interval '14 hours', 180, 'operating_theatre'
FROM generate_series('2026-10-01'::timestamp, '2026-12-31'::timestamp, interval '1 day') AS day
WHERE extract(isodow FROM day) IN (2, 4);

-- ---------- Appointments ----------
INSERT INTO appointments (appointment_id, patient_id, doctor_id, slot_id, appointment_type, department, reason, status)
SELECT v.appointment_id, v.patient_id, v.doctor_id, s.slot_id, v.appointment_type, d.department, v.reason, v.status
FROM (VALUES
  ('A5001','P1001','D202','2026-11-20 10:30'::timestamp,'consultation', 'Cardiac fitness review before knee surgery','scheduled'),
  ('A5002','P1001','D201','2026-11-26 14:00'::timestamp,'surgery',      'Total knee replacement (right knee)',        'scheduled'),
  ('A5003','P1001','D205','2026-11-23 11:00'::timestamp,'pre_op_review','Pre-anaesthesia assessment',                'scheduled'),
  ('A5004','P1001','D204','2026-11-10 09:30'::timestamp,'consultation', 'Diabetes review before surgery',            'completed'),
  ('A5005','P1001','D201','2026-10-28 10:00'::timestamp,'consultation', 'Right knee pain – surgery recommended',     'completed'),
  ('A5006','P1002','D203','2026-11-24 10:30'::timestamp,'imaging',      'MRI brain with contrast',                   'scheduled'),
  ('A5007','P1002','D207','2026-11-05 10:00'::timestamp,'consultation', 'Recurring headaches',                       'completed'),
  ('A5011','P1002','D207','2026-11-12 09:00'::timestamp,'follow_up',    'Headache follow-up',                        'cancelled'),
  ('A5008','P1003','D202','2026-12-02 09:00'::timestamp,'follow_up',    'Anticoagulation and heart-rate review',     'scheduled'),
  ('A5009','P1003','D202','2026-10-15 09:30'::timestamp,'follow_up',    'Post-discharge review',                     'completed'),
  ('A5010','P1004','D201','2026-11-19 11:30'::timestamp,'consultation', 'Knee pain evaluation',                      'scheduled')
) AS v(appointment_id, patient_id, doctor_id, start_time, appointment_type, reason, status)
JOIN appointment_slots s ON s.doctor_id = v.doctor_id AND s.start_time = v.start_time
JOIN doctors d ON d.doctor_id = v.doctor_id;

-- Booked slots are not available
UPDATE appointment_slots SET is_available = false
WHERE slot_id IN (SELECT slot_id FROM appointments WHERE status IN ('scheduled','completed'));

-- Make cardiology busy on 23–24 Nov (other patients' bookings) so rescheduling is realistic
UPDATE appointment_slots SET is_available = false
WHERE doctor_id = 'D202' AND start_time::date IN ('2026-11-23','2026-11-24')
  AND extract(minute FROM start_time) = 0;

-- ---------- Medical orders (EMR) ----------
INSERT INTO medical_orders (order_id, patient_id, ordered_by, order_type, item_code, item_name, related_procedure_code, status, ordered_at, completed_at) VALUES
('O7009','P1001','D201','radiology', 'XR_KNEE',          'X-ray right knee',               NULL, 'completed','2026-10-28 10:20','2026-10-28 11:00'),
('O7007','P1001','D201','procedure', 'TKR',              'Total knee replacement (right)', 'TKR','ordered',  '2026-10-28 10:30', NULL),
('O7001','P1001','D201','lab',       'CBC',              'Complete Blood Count',           'TKR','completed','2026-10-28 10:30','2026-11-10 14:00'),
('O7002','P1001','D201','lab',       'HBA1C',            'HbA1c',                          'TKR','completed','2026-10-28 10:30','2026-11-10 14:00'),
('O7003','P1001','D201','diagnostic','ECG',              '12-lead ECG',                    'TKR','completed','2026-10-28 10:30','2026-11-12 11:00'),
('O7004','P1001','D201','radiology', 'CXR',              'Chest X-ray (PA view)',          'TKR','ordered',  '2026-10-28 10:30', NULL),
('O7005','P1001','D201','lab',       'PT_INR',           'Coagulation profile (PT/INR)',   'TKR','ordered',  '2026-10-28 10:30', NULL),
('O7006','P1001','D201','consult',   'ANESTHESIA_REVIEW','Pre-anaesthesia review',         'TKR','ordered',  '2026-10-28 10:30', NULL),
('O7010','P1002','D207','radiology', 'MRI_BRAIN',        'MRI brain with contrast',        NULL, 'ordered',  '2026-11-05 10:30', NULL),
('O7022','P1003','D202','radiology', 'CXR',              'Chest X-ray (PA view)',          NULL, 'completed','2026-09-12 18:00','2026-09-13 09:00'),
('O7020','P1003','D202','lab',       'INR',              'INR (warfarin monitoring)',      NULL, 'completed','2026-11-13 09:00','2026-11-14 15:00'),
('O7021','P1003','D202','lab',       'LDL',              'LDL cholesterol',                NULL, 'completed','2026-11-13 09:00','2026-11-14 15:00');

-- ---------- Pre-op protocol (structured mirror of DOC003 §4) ----------
INSERT INTO preop_requirements (procedure_code, item_code, item_name, validity_days, applies_to, rule, source_document) VALUES
('TKR','CBC',              'Complete Blood Count',          30, 'all',      NULL,                          'DOC003 §4.1'),
('TKR','PT_INR',           'Coagulation profile (PT/INR)',  30, 'all',      NULL,                          'DOC003 §4.1'),
('TKR','ECG',              '12-lead ECG',                   30, 'all',      NULL,                          'DOC003 §4.1'),
('TKR','CXR',              'Chest X-ray (PA view)',         90, 'all',      NULL,                          'DOC003 §4.1'),
('TKR','HBA1C',            'HbA1c',                         90, 'diabetes', 'HbA1c must be below 8.0 %',   'DOC003 §4.2'),
('TKR','ANESTHESIA_REVIEW','Pre-anaesthesia review',        30, 'all',      NULL,                          'DOC003 §4.3');

INSERT INTO procedure_readiness (patient_id, procedure_code, item_code, item_name, status) VALUES
('P1001','TKR','CONSENT_SIGNED',     'Surgical consent form signed',                 'done'),
('P1001','TKR','FASTING_INSTRUCTIONS','Fasting instructions acknowledged',           'pending'),
('P1001','TKR','METFORMIN_HOLD',     'Metformin hold plan confirmed with physician', 'pending'),
('P1001','TKR','DISCHARGE_ESCORT',   'Adult escort arranged for discharge',          'pending');

-- ---------- Lab results (LIS) ----------
INSERT INTO lab_results (result_id, patient_id, order_id, test_code, test_name, value, unit, ref_low, ref_high, flag, collected_at, reported_at) VALUES
('L3001','P1001',NULL,   'HBA1C','HbA1c',                    8.1, '%',        4.0, 5.6,  'high',  '2026-05-12 08:30','2026-05-12 15:00'),
('L3002','P1001',NULL,   'HBA1C','HbA1c',                    7.6, '%',        4.0, 5.6,  'high',  '2026-08-14 08:30','2026-08-14 15:00'),
('L3003','P1001',NULL,   'FBS',  'Fasting blood glucose',    145, 'mg/dL',    70,  99,   'high',  '2026-08-14 08:30','2026-08-14 15:00'),
('L3004','P1001','O7002','HBA1C','HbA1c',                    7.2, '%',        4.0, 5.6,  'high',  '2026-11-10 08:30','2026-11-10 14:00'),
('L3005','P1001',NULL,   'FBS',  'Fasting blood glucose',    132, 'mg/dL',    70,  99,   'high',  '2026-11-10 08:30','2026-11-10 14:00'),
('L3006','P1001','O7001','HGB',  'Haemoglobin',              13.8,'g/dL',     13.0,17.0, 'normal','2026-11-10 08:30','2026-11-10 14:00'),
('L3007','P1001','O7001','WBC',  'White blood cell count',   7.2, '10^3/uL',  4.0, 11.0, 'normal','2026-11-10 08:30','2026-11-10 14:00'),
('L3008','P1001','O7001','PLT',  'Platelet count',           250, '10^3/uL',  150, 400,  'normal','2026-11-10 08:30','2026-11-10 14:00'),
('L3009','P1001',NULL,   'CREAT','Serum creatinine',         1.0, 'mg/dL',    0.7, 1.3,  'normal','2026-11-10 08:30','2026-11-10 14:00'),
('L3010','P1002',NULL,   'TSH',  'Thyroid stimulating hormone',3.1,'mIU/L',   0.4, 4.0,  'normal','2026-11-05 09:00','2026-11-05 16:00'),
('L3011','P1002',NULL,   'HGB',  'Haemoglobin',              11.2,'g/dL',     12.0,15.5, 'low',   '2026-11-05 09:00','2026-11-05 16:00'),
('L3012','P1003','O7020','INR',  'INR',                      2.4, 'ratio',    0.8, 1.2,  'high',  '2026-11-14 08:00','2026-11-14 15:00'),
('L3013','P1003','O7021','LDL',  'LDL cholesterol',          98,  'mg/dL',    0,   100,  'normal','2026-11-14 08:00','2026-11-14 15:00'),
('L3014','P1004',NULL,   'HGB',  'Haemoglobin',              12.9,'g/dL',     12.0,15.5, 'normal','2026-11-02 09:00','2026-11-02 16:00');

-- ---------- Radiology reports (RIS) ----------
INSERT INTO radiology_reports (report_id, patient_id, order_id, modality, body_part, findings, impression, radiologist_id, performed_at) VALUES
('R4001','P1001','O7009','X-ray','Right knee',
 'Marked narrowing of the medial joint space with osteophytes and subchondral sclerosis.',
 'Severe osteoarthritis of the right knee.','D203','2026-10-28 10:45'),
('R4002','P1003','O7022','X-ray','Chest',
 'Mildly enlarged cardiac silhouette. Lung fields clear. No effusion.',
 'Mild cardiomegaly; no acute lung findings.','D203','2026-09-13 08:30');

-- ---------- Insurance ----------
INSERT INTO insurance_plans (plan_id, provider, plan_name, annual_limit) VALUES
('NW_GOLD',  'Northwind Health', 'Gold Plan',  500000),
('CT_BASIC', 'Contoso Care',     'Basic Plan', 200000);

INSERT INTO insurance_policies (policy_id, patient_id, plan_id, policy_number, member_since, valid_from, valid_to, status) VALUES
('POL9001','P1001','NW_GOLD', 'NWG-2023-114455','2023-04-01','2026-04-01','2027-03-31','active'),
('POL9002','P1002','CT_BASIC','CTB-2025-778120','2025-06-01','2026-06-01','2027-05-31','active'),
('POL9003','P1003','NW_GOLD', 'NWG-2021-009331','2021-10-01','2025-10-01','2026-09-30','expired'),
('POL9004','P1004','NW_GOLD', 'NWG-2026-551902','2026-08-01','2026-08-01','2027-07-31','active');

-- Northwind Gold v2.0 rules (DOC007) — valid until v3.0 (DOC008) starts on 2027-01-01
INSERT INTO coverage_rules (rule_id, plan_id, procedure_code, coverage_status, coverage_percent, max_amount, requires_preauth, waiting_period_days, conditions, effective_from, effective_to, source_document) VALUES
('CR001','NW_GOLD','TKR',           'covered',     80, 350000, true,  730, '{"required_diagnosis": "osteoarthritis_knee"}', '2026-04-01','2026-12-31','DOC007 §3.2'),
('CR002','NW_GOLD','MRI_BRAIN',     'covered',    100,  25000, false,   0, '{}', '2026-04-01','2026-12-31','DOC007 §3.3'),
('CR003','NW_GOLD','MRI_KNEE',      'covered',    100,  25000, false,   0, '{}', '2026-04-01','2026-12-31','DOC007 §3.3'),
('CR004','NW_GOLD','XR_KNEE',       'covered',     80,   NULL, false,   0, '{}', '2026-04-01','2026-12-31','DOC007 §3.4'),
('CR005','NW_GOLD','CXR',           'covered',     80,   NULL, false,   0, '{}', '2026-04-01','2026-12-31','DOC007 §3.4'),
('CR006','NW_GOLD','ECG',           'covered',     80,   NULL, false,   0, '{}', '2026-04-01','2026-12-31','DOC007 §3.4'),
('CR007','NW_GOLD','CBC',           'covered',     80,   NULL, false,   0, '{}', '2026-04-01','2026-12-31','DOC007 §3.4'),
('CR008','NW_GOLD','HBA1C',         'covered',     80,   NULL, false,   0, '{}', '2026-04-01','2026-12-31','DOC007 §3.4'),
('CR009','NW_GOLD','PT_INR',        'covered',     80,   NULL, false,   0, '{}', '2026-04-01','2026-12-31','DOC007 §3.4'),
('CR010','NW_GOLD','ORTHO_CONSULT', 'covered',    100,   2000, false,   0, '{}', '2026-04-01','2026-12-31','DOC007 §3.5'),
('CR011','NW_GOLD','CARDIO_CONSULT','covered',    100,   2000, false,   0, '{}', '2026-04-01','2026-12-31','DOC007 §3.5'),
('CR012','NW_GOLD','GEN_CONSULT',   'covered',    100,   2000, false,   0, '{}', '2026-04-01','2026-12-31','DOC007 §3.5'),
('CR013','NW_GOLD','RHINOPLASTY',   'not_covered',  0,   NULL, false,   0, '{"exclusion": "cosmetic procedures"}', '2026-04-01','2026-12-31','DOC007 §4.1'),
-- Northwind Gold v3.0 (DOC008) — NOT yet effective (starts 2027-01-01)
('CR101','NW_GOLD','TKR',           'covered',     90, 400000, true,  730, '{"required_diagnosis": "osteoarthritis_knee"}', '2027-01-01', NULL,'DOC008 §3.2'),
-- Contoso Basic v1.0 (DOC009)
('CR201','CT_BASIC','MRI_BRAIN',    'partial',     50,  10000, false,   0, '{}', '2025-06-01', NULL,'DOC009 §3.2'),
('CR202','CT_BASIC','MRI_KNEE',     'partial',     50,  10000, false,   0, '{}', '2025-06-01', NULL,'DOC009 §3.2'),
('CR203','CT_BASIC','TKR',          'not_covered',  0,   NULL, false,   0, '{"exclusion": "joint replacement surgery"}', '2025-06-01', NULL,'DOC009 §4.1'),
('CR204','CT_BASIC','GEN_CONSULT',  'covered',    100,   1000, false,   0, '{}', '2025-06-01', NULL,'DOC009 §3.1'),
('CR205','CT_BASIC','CARDIO_CONSULT','covered',   100,   1000, false,   0, '{}', '2025-06-01', NULL,'DOC009 §3.1'),
('CR206','CT_BASIC','CBC',          'covered',    100,    500, false,   0, '{}', '2025-06-01', NULL,'DOC009 §3.3'),
('CR207','CT_BASIC','RHINOPLASTY',  'not_covered',  0,   NULL, false,   0, '{"exclusion": "cosmetic procedures"}', '2025-06-01', NULL,'DOC009 §4.1');

INSERT INTO prior_authorizations (auth_id, patient_id, policy_id, procedure_code, status, requested_at) VALUES
('PA6001','P1001','POL9001','TKR','pending','2026-11-12 10:00');

-- ---------- Billing ----------
INSERT INTO bills (bill_id, patient_id, description, total_amount, insurance_covered, patient_payable, amount_paid, status, issued_at, due_date) VALUES
('B8001','P1001','Pre-operative tests (10–12 Nov 2026)',            1500,  1200,   300,     0, 'unpaid',         '2026-11-12','2026-11-30'),
('B8002','P1002','General medicine consultation (5 Nov 2026)',       800,   800,     0,     0, 'paid',           '2026-11-05','2026-11-20'),
('B8003','P1003','Inpatient admission – cardiology (12–15 Sep 2026)',85000, 60000, 25000, 10000, 'partially_paid', '2026-09-15','2026-10-15');

INSERT INTO bill_items (bill_id, procedure_code, description, amount) VALUES
('B8001','CBC',        'Complete Blood Count',          400),
('B8001','HBA1C',      'HbA1c',                         600),
('B8001','ECG',        '12-lead ECG',                   500),
('B8002','GEN_CONSULT','General medicine consultation', 800),
('B8003',NULL,         'Room charges (3 days)',       30000),
('B8003',NULL,         'Cardiac monitoring',          25000),
('B8003',NULL,         'Medications',                 15000),
('B8003',NULL,         'Echocardiogram',              15000);

-- ---------- Pharmacy ----------
INSERT INTO medications (drug_code, generic_name, brand_name, drug_class) VALUES
('METFORMIN',        'Metformin',         NULL, 'Biguanide (antidiabetic)'),
('ATORVASTATIN',     'Atorvastatin',      NULL, 'Statin'),
('LEVOTHYROXINE',    'Levothyroxine',     NULL, 'Thyroid hormone'),
('WARFARIN',         'Warfarin',          NULL, 'Anticoagulant'),
('ASPIRIN',          'Aspirin',           NULL, 'Antiplatelet'),
('IBUPROFEN',        'Ibuprofen',         NULL, 'NSAID'),
('CLARITHROMYCIN',   'Clarithromycin',    NULL, 'Macrolide antibiotic'),
('IOHEXOL',          'Iohexol',           NULL, 'Iodinated contrast agent'),
('CALCIUM_CARBONATE','Calcium carbonate', NULL, 'Mineral supplement / antacid'),
('PARACETAMOL',      'Paracetamol',       NULL, 'Analgesic');

INSERT INTO prescriptions (prescription_id, patient_id, doctor_id, drug_code, dose, frequency, start_date, end_date, status) VALUES
('RX2001','P1001','D204','METFORMIN',    '500 mg', 'twice daily',             '2024-01-10', NULL,        'active'),
('RX2002','P1001','D204','ATORVASTATIN', '20 mg',  'once daily at night',     '2024-01-10', NULL,        'active'),
('RX2003','P1002','D207','LEVOTHYROXINE','50 mcg', 'once daily before breakfast','2025-03-01', NULL,     'active'),
('RX2004','P1003','D202','WARFARIN',     '5 mg',   'once daily',              '2026-09-15', NULL,        'active'),
('RX2005','P1003','D202','ASPIRIN',      '75 mg',  'once daily',              '2026-09-15', NULL,        'active'),
('RX2006','P1003','D207','IBUPROFEN',    '400 mg', 'as needed for pain',      '2026-08-01','2026-09-10', 'stopped');

-- Synthetic formulary for development only — NOT clinical guidance.
INSERT INTO drug_interactions (drug_a, drug_b, severity, description, source) VALUES
('ASPIRIN','WARFARIN',                 'major',    'Increased bleeding risk when combined; requires prescriber review and INR monitoring.', 'Synthetic formulary v1'),
('IBUPROFEN','WARFARIN',               'major',    'NSAIDs increase bleeding risk with anticoagulants.',                                  'Synthetic formulary v1'),
('ASPIRIN','IBUPROFEN',                'moderate', 'Ibuprofen may reduce the antiplatelet effect of low-dose aspirin.',                   'Synthetic formulary v1'),
('ATORVASTATIN','CLARITHROMYCIN',      'major',    'Raises statin levels; increased risk of muscle toxicity.',                            'Synthetic formulary v1'),
('IOHEXOL','METFORMIN',                'moderate', 'Metformin is usually held around iodinated contrast per hospital protocol.',          'Synthetic formulary v1'),
('CALCIUM_CARBONATE','LEVOTHYROXINE',  'moderate', 'Calcium reduces levothyroxine absorption; separate doses by 4 hours.',                 'Synthetic formulary v1');
