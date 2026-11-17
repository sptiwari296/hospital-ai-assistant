"""Phase 4 tests: deterministic tools against the real database, clock frozen at 18 Nov 2026 09:00."""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app import clock
from app.db import connect
from app.tools import appointments
from app.tools.base import AuthorizationError, build_context
from app.tools.registry import TOOLS, run_tool

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 18, 9, 0)


@pytest.fixture(autouse=True)
def frozen_clock():
    with clock.frozen_at(NOW):
        yield


@pytest.fixture
def rahul():
    return build_context("rahul.sharma", trace_id="TEST")


@pytest.fixture
def restore_a5001():
    """Put appointment A5001 and its slots back exactly as seeded, and remove test confirmations."""
    with connect() as conn:
        orig = conn.execute("SELECT slot_id, doctor_id, status FROM appointments WHERE appointment_id='A5001'").fetchone()
    yield orig
    with connect() as conn:
        cur = conn.execute("SELECT slot_id FROM appointments WHERE appointment_id='A5001'").fetchone()
        conn.execute("UPDATE appointment_slots SET is_available = true WHERE slot_id = %s", (cur["slot_id"],))
        conn.execute("UPDATE appointments SET slot_id=%s, doctor_id=%s, status=%s WHERE appointment_id='A5001'",
                     (orig["slot_id"], orig["doctor_id"], orig["status"]))
        conn.execute("UPDATE appointment_slots SET is_available = false WHERE slot_id = %s", (orig["slot_id"],))
        conn.execute("DELETE FROM pending_actions WHERE trace_id = 'TEST'")


def a5001(conn=None):
    with connect() as c:
        return c.execute("""SELECT a.slot_id, a.status, s.start_time FROM appointments a JOIN appointment_slots s
                            USING (slot_id) WHERE appointment_id = 'A5001'""").fetchone()


# ---------------------------------------------------------------- registry safety
def test_no_tool_accepts_patient_id_and_llm_cannot_confirm():
    for spec in TOOLS.values():
        assert "patient_id" not in spec.input_model.model_fields, spec.name
    assert "confirm_action" not in TOOLS and "decline_action" not in TOOLS


def test_injected_fields_rejected(rahul):
    r = run_tool("get_lab_results", rahul, {"test_name": "INR", "patient_id": "P1003"})
    assert r.status == "invalid" and "Extra inputs" in r.summary


# ---------------------------------------------------------------- authorization
def test_patient_cannot_act_for_another_patient():
    with pytest.raises(AuthorizationError):
        build_context("rahul.sharma", acting_for_patient="P1003")


def test_staff_only_assigned_patients():
    assert build_context("nurse.anita", acting_for_patient="P1001").patient_id == "P1001"
    with pytest.raises(AuthorizationError, match="not assigned"):
        build_context("nurse.deepak", acting_for_patient="P1001")


def test_admin_has_no_patient_access():
    r = run_tool("get_appointments", build_context("admin_01"), {})
    assert r.status == "denied" and r.answer_type == "refused"


def test_other_patients_records_look_not_found(rahul):
    assert run_tool("get_payment_status", rahul, {"invoice_id": "B8003"}).summary.endswith("not found in your records.")
    assert "not found" in run_tool("cancel_appointment", rahul, {"appointment_id": "A5006"}).summary


# ---------------------------------------------------------------- Tool 1: appointments
def test_upcoming_appointments(rahul):
    r = run_tool("get_appointments", rahul, {})
    assert [a["appointment_id"] for a in r.data["appointments"]] == ["A5001", "A5003", "A5002"]


def test_alternative_slots_same_department_from_tomorrow(rahul):
    slots = run_tool("get_available_slots", rahul, {"appointment_id": "A5001"}).data["slots"]
    assert slots and all(s["department"] == "Cardiology" for s in slots)
    assert all(s["date_time"] >= "2026-11-19" for s in slots)


def test_reschedule_proposal_changes_nothing_then_confirm_executes_and_verifies(rahul, restore_a5001):
    slot = run_tool("get_available_slots", rahul, {"appointment_id": "A5001"}).data["slots"][0]
    proposal = run_tool("reschedule_appointment", rahul, {"appointment_id": "A5001", "slot_id": slot["slot_id"]})
    assert proposal.status == "requires_confirmation" and "Nothing has been changed" in proposal.summary
    assert a5001()["slot_id"] == restore_a5001["slot_id"]                      # step 5: still unchanged

    done = appointments.confirm_action(rahul, proposal.data["confirmation_id"])
    assert done.answer_type == "action_completed" and "verified" in done.summary
    after = a5001()
    assert after["slot_id"] == slot["slot_id"] and after["status"] == "scheduled"
    with connect() as conn:
        free = {r["slot_id"]: r["is_available"] for r in conn.execute(
            "SELECT slot_id, is_available FROM appointment_slots WHERE slot_id IN (%s, %s)",
            (slot["slot_id"], restore_a5001["slot_id"])).fetchall()}
        logged = conn.execute("SELECT status FROM pending_actions WHERE confirmation_id = %s",
                              (proposal.data["confirmation_id"],)).fetchone()
    assert free == {slot["slot_id"]: False, restore_a5001["slot_id"]: True}
    assert logged["status"] == "executed"
    again = appointments.confirm_action(rahul, proposal.data["confirmation_id"])
    assert again.status == "invalid" and "already executed" in again.summary


def test_confirmation_belongs_to_its_user(rahul, restore_a5001):
    slot = run_tool("get_available_slots", rahul, {"appointment_id": "A5001"}).data["slots"][0]
    cid = run_tool("reschedule_appointment", rahul, {"appointment_id": "A5001", "slot_id": slot["slot_id"]}) \
        .data["confirmation_id"]
    assert appointments.confirm_action(build_context("priya.nair"), cid).answer_type == "refused"
    assert a5001()["slot_id"] == restore_a5001["slot_id"]


def test_expired_confirmation_not_executed(rahul, restore_a5001):
    slot = run_tool("get_available_slots", rahul, {"appointment_id": "A5001"}).data["slots"][0]
    cid = run_tool("reschedule_appointment", rahul, {"appointment_id": "A5001", "slot_id": slot["slot_id"]}) \
        .data["confirmation_id"]
    with clock.frozen_at(NOW + timedelta(minutes=11)):
        assert "expired" in appointments.confirm_action(rahul, cid).summary
    assert a5001()["slot_id"] == restore_a5001["slot_id"]


def test_slot_taken_before_confirmation_fails_safely(rahul, restore_a5001):
    slot = run_tool("get_available_slots", rahul, {"appointment_id": "A5001"}).data["slots"][0]
    cid = run_tool("reschedule_appointment", rahul, {"appointment_id": "A5001", "slot_id": slot["slot_id"]}) \
        .data["confirmation_id"]
    with connect() as conn:  # someone else books it meanwhile
        conn.execute("UPDATE appointment_slots SET is_available = false WHERE slot_id = %s", (slot["slot_id"],))
    try:
        r = appointments.confirm_action(rahul, cid)
        assert r.status == "error" and "NOT made" in r.summary
        assert a5001()["slot_id"] == restore_a5001["slot_id"]
    finally:
        with connect() as conn:
            conn.execute("UPDATE appointment_slots SET is_available = true WHERE slot_id = %s", (slot["slot_id"],))


def test_cancel_flow(rahul, restore_a5001):
    cid = run_tool("cancel_appointment", rahul, {"appointment_id": "A5001"}).data["confirmation_id"]
    assert a5001()["status"] == "scheduled"
    assert appointments.confirm_action(rahul, cid).answer_type == "action_completed"
    assert a5001()["status"] == "cancelled"


def test_surgery_and_late_changes_refused(rahul):
    assert "surgical team" in run_tool("cancel_appointment", rahul, {"appointment_id": "A5002"}).summary
    with clock.frozen_at(datetime(2026, 11, 19, 12, 0)):  # A5001 is 22.5 h away
        assert "24 hours" in run_tool("cancel_appointment", rahul, {"appointment_id": "A5001"}).summary


# ---------------------------------------------------------------- Tool 2: insurance
@pytest.mark.parametrize("user, procedure, service_date, status, insurer", [
    ("rahul.sharma", "knee replacement", date(2026, 11, 26), "requires_pre_authorization", Decimal("240000.00")),
    ("rahul.sharma", "TKR", date(2027, 2, 1), "requires_pre_authorization", Decimal("270000.00")),  # 2027 agreement
    ("priya.nair", "knee replacement", None, "not_covered", Decimal("0.00")),
    ("priya.nair", "MRI brain", None, "eligible", Decimal("6000.00")),
    ("arjun.mehta", "cardiology consultation", None, "not_eligible", Decimal("0.00")),             # expired policy
    ("sneha.kapoor", "knee replacement", None, "not_eligible", Decimal("0.00")),                   # waiting period
    ("rahul.sharma", "rhinoplasty", None, "not_covered", Decimal("0.00")),
])
def test_insurance_rules(user, procedure, service_date, status, insurer):
    r = run_tool("check_insurance_eligibility", build_context(user),
                 {"procedure": procedure, "service_date": service_date})
    assert r.answer_type == "calculated" and r.data["eligibility_status"] == status
    assert r.data["insurer_pays"] == insurer


def test_insurance_network_restriction(rahul):
    with connect() as conn:
        conn.execute("UPDATE insurance_plans SET hospital_in_network = false WHERE plan_id = 'NW_GOLD'")
    try:
        r = run_tool("check_insurance_eligibility", rahul, {"procedure": "MRI knee"})
        assert r.data["eligibility_status"] == "not_eligible" and "network" in r.summary
    finally:
        with connect() as conn:
            conn.execute("UPDATE insurance_plans SET hospital_in_network = true WHERE plan_id = 'NW_GOLD'")


def test_policy_number_masked(rahul):
    assert run_tool("check_insurance_eligibility", rahul, {"procedure": "TKR"}).data["policy_number"].endswith("4455")
    assert "114455" not in run_tool("check_insurance_eligibility", rahul, {"procedure": "TKR"}).model_dump_json()


# ---------------------------------------------------------------- Tool 3: labs
def test_blood_sugar_maps_to_fasting_glucose(rahul):
    r = run_tool("get_lab_results", rahul, {"test_name": "blood sugar", "limit": 1})
    assert r.data["latest"]["value"] == 132 and r.data["latest"]["unit"] == "mg/dL"
    assert r.data["latest"]["report_reference"] == "LIS result L3005"


def test_hba1c_history_latest_first_and_no_diagnosis(rahul):
    r = run_tool("get_lab_results", rahul, {"test_name": "HbA1c"})
    assert [x["value"] for x in r.data["results"]] == [7.2, 7.6, 8.1]
    assert "not a diagnosis" in r.notes[0] and "diabet" not in r.summary.lower()


def test_unknown_test_and_missing_results():
    assert run_tool("get_lab_results", build_context("rahul.sharma"), {"test_name": "vitamin D"}).answer_type \
        == "clarification_needed"
    assert run_tool("get_lab_results", build_context("priya.nair"), {"test_name": "HbA1c"}).status == "not_found"


# ---------------------------------------------------------------- Tool 4: medications
def test_interactions_from_formulary_only(rahul):
    r = run_tool("drug_interaction_check", rahul, {"medication_list": ["warfarin", "Aspirin", "Crocin"]})
    assert r.data["highest_severity"] == "major" and r.data["unrecognised"] == ["Crocin"]
    none = run_tool("drug_interaction_check", rahul, {"medication_list": ["paracetamol", "metformin"]})
    assert none.data["interactions"] == [] and "does not guarantee" in none.summary


def test_new_medication_checked_against_prescriptions(rahul):
    r = run_tool("check_new_medication", rahul, {"medication_list": ["clarithromycin"]})
    assert r.data["interactions"][0]["medicines"] == ["Atorvastatin", "Clarithromycin"]


# ---------------------------------------------------------------- Tool 5: billing
def test_bills_and_overdue():
    r = run_tool("get_bills", build_context("arjun.mehta"), {})
    assert r.data["total_outstanding"] == Decimal("15000.00") and r.data["bills"][0]["overdue"]


def test_estimates_use_insurance_rules():
    rahul = run_tool("calculate_estimated_bill", build_context("rahul.sharma"), {"procedure": "TKR"})
    assert rahul.data["patient_pays"] == Decimal("60000.00") and "pre-authorisation" in rahul.summary
    priya = run_tool("calculate_estimated_bill", build_context("priya.nair"), {"procedure": "TKR"})
    assert priya.data["patient_pays"] == Decimal("300000.00")


# ---------------------------------------------------------------- Tool 6: care coordination
def test_preop_status_against_surgery_date(rahul):
    r = run_tool("get_preoperative_test_status", rahul, {})
    status = {i["code"]: i["status"] for i in r.data["items"]}
    assert status == {"ANESTHESIA_REVIEW": "pending", "CBC": "done", "CXR": "pending", "ECG": "done",
                      "HBA1C": "done", "PT_INR": "pending"}
    assert r.data["surgery_date"] == "2026-11-26" and "not medical clearance" in r.notes[0]


def test_hba1c_rule_breach_detected(rahul):
    with connect() as conn:
        conn.execute("""INSERT INTO lab_results (result_id, patient_id, test_code, test_name, value, unit, ref_low,
                        ref_high, flag, collected_at, reported_at) VALUES ('LTEST','P1001','HBA1C','HbA1c',8.4,'%%',
                        4.0,5.6,'high','2026-11-17 08:00','2026-11-17 12:00')""")
    try:
        items = run_tool("get_preoperative_test_status", rahul, {}).data["items"]
        assert next(i for i in items if i["code"] == "HBA1C")["status"] == "rule_not_met"
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM lab_results WHERE result_id = 'LTEST'")


def test_checklist_is_administrative_only(rahul):
    r = run_tool("get_readiness_checklist", rahul, {})
    assert r.data["administratively_complete"] is False and "not medical clearance" in r.notes[0]
    assert "clear" not in r.summary.lower()


def test_surgeon_availability(rahul):
    r = run_tool("get_surgeon_availability", rahul, {})
    assert r.data["scheduled_surgery"]["appointment_id"] == "A5002"
    assert all(s["surgeon"] == "Dr. Vikram Rao" for s in r.data["free_theatre_slots"])
