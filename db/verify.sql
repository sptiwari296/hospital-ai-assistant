-- Sanity checks for Phase 1 data. Run:
--   docker exec -i hospital_db psql -U hospital_app -d hospital < db/verify.sql

\echo '=== Row counts ==='
SELECT 'patients' t, count(*) FROM patients UNION ALL
SELECT 'users', count(*) FROM users UNION ALL
SELECT 'doctors', count(*) FROM doctors UNION ALL
SELECT 'appointment_slots', count(*) FROM appointment_slots UNION ALL
SELECT 'appointments (expect 11)', count(*) FROM appointments UNION ALL
SELECT 'medical_orders', count(*) FROM medical_orders UNION ALL
SELECT 'lab_results', count(*) FROM lab_results UNION ALL
SELECT 'coverage_rules', count(*) FROM coverage_rules UNION ALL
SELECT 'bills', count(*) FROM bills UNION ALL
SELECT 'prescriptions', count(*) FROM prescriptions UNION ALL
SELECT 'drug_interactions', count(*) FROM drug_interactions;

\echo '=== Personal data: "What are my upcoming appointments?" (P1001) ==='
SELECT a.appointment_id, s.start_time, d.full_name AS doctor, a.department, a.appointment_type
FROM appointments a JOIN appointment_slots s USING (slot_id) JOIN doctors d ON d.doctor_id = a.doctor_id
WHERE a.patient_id = 'P1001' AND a.status = 'scheduled' AND s.start_time > '2026-11-18'
ORDER BY s.start_time;

\echo '=== Lab: "What is my latest HbA1c?" (P1001) ==='
SELECT test_name, value, unit, flag, collected_at FROM lab_results
WHERE patient_id = 'P1001' AND test_code = 'HBA1C' ORDER BY collected_at DESC LIMIT 1;

\echo '=== Multi-step: TKR pre-op requirements vs completed orders (P1001) ==='
SELECT r.item_code, r.item_name, r.applies_to, coalesce(o.status, 'not ordered') AS order_status, o.completed_at
FROM preop_requirements r
LEFT JOIN medical_orders o ON o.patient_id = 'P1001' AND o.item_code = r.item_code
WHERE r.procedure_code = 'TKR' ORDER BY r.item_code;

\echo '=== Eligibility inputs: TKR coverage rule in force today for each patient ==='
SELECT p.patient_id, pol.status AS policy_status, pol.member_since, cr.coverage_status, cr.coverage_percent,
       cr.requires_preauth, cr.waiting_period_days,
       (DATE '2026-11-18' - pol.member_since) AS days_as_member
FROM insurance_policies pol
JOIN patients p USING (patient_id)
LEFT JOIN coverage_rules cr ON cr.plan_id = pol.plan_id AND cr.procedure_code = 'TKR'
     AND DATE '2026-11-18' BETWEEN cr.effective_from AND coalesce(cr.effective_to, 'infinity')
ORDER BY p.patient_id;

\echo '=== Pharmacy: interactions among P1003 active prescriptions ==='
SELECT i.drug_a, i.drug_b, i.severity FROM drug_interactions i
WHERE i.drug_a IN (SELECT drug_code FROM prescriptions WHERE patient_id='P1003' AND status='active')
  AND i.drug_b IN (SELECT drug_code FROM prescriptions WHERE patient_id='P1003' AND status='active');

\echo '=== Free cardiology slots 20–24 Nov (for rescheduling A5001) ==='
SELECT doctor_id, count(*) AS free_slots FROM appointment_slots
WHERE doctor_id IN ('D202','D206') AND is_available AND start_time BETWEEN '2026-11-20' AND '2026-11-25'
GROUP BY doctor_id;
