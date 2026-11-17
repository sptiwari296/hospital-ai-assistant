"""Walk through every operational tool with the synthetic patients (no LLM involved).

Run:  .venv\\Scripts\\python scripts\\tools_demo.py
The clock is frozen at 18 Nov 2026 09:00 to match the seed data. The reschedule is
PROPOSED and then DECLINED, so the database is left unchanged.
"""
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import clock  # noqa: E402
from app.tools import appointments  # noqa: E402
from app.tools.base import AuthorizationError, build_context  # noqa: E402
from app.tools.registry import run_tool  # noqa: E402


def show(title: str, result) -> None:
    print(f"\n=== {title}\n[{result.tool} | {result.status} | {result.answer_type} | {result.source}]")
    print(result.summary)
    if result.references:
        print("refs:", ", ".join(result.references))
    for note in result.notes:
        print("note:", note)


with clock.frozen_at(datetime(2026, 11, 18, 9, 0)):
    rahul = build_context("rahul.sharma", trace_id="HSP-DEMO")
    show("Rahul: upcoming appointments", run_tool("get_appointments", rahul, {}))
    show("Rahul: alternatives for cardiology A5001", run_tool("get_available_slots", rahul, {"appointment_id": "A5001"}))
    slot = run_tool("get_available_slots", rahul, {"appointment_id": "A5001"}).data["slots"][0]["slot_id"]
    proposal = run_tool("reschedule_appointment", rahul, {"appointment_id": "A5001", "slot_id": slot})
    show("Rahul: propose reschedule (nothing changes yet)", proposal)
    show("Rahul: says NO", appointments.decline_action(rahul, proposal.data["confirmation_id"]))
    show("Rahul: try to move his SURGERY", run_tool("reschedule_appointment", rahul, {"appointment_id": "A5002", "slot_id": 1}))
    show("Rahul: latest blood sugar", run_tool("get_lab_results", rahul, {"test_name": "blood sugar", "limit": 1}))
    show("Rahul: HbA1c history", run_tool("get_lab_results", rahul, {"test_name": "HbA1c"}))
    show("Rahul: knee replacement coverage on surgery date",
         run_tool("check_insurance_eligibility", rahul, {"procedure": "knee replacement", "service_date": date(2026, 11, 26)}))
    show("Rahul: estimated bill", run_tool("calculate_estimated_bill", rahul, {"procedure": "TKR"}))
    show("Rahul: pre-op test status", run_tool("get_preoperative_test_status", rahul, {}))
    show("Rahul: surgeon availability", run_tool("get_surgeon_availability", rahul, {}))
    show("Rahul: readiness checklist", run_tool("get_readiness_checklist", rahul, {}))
    show("Rahul: can I take clarithromycin?", run_tool("check_new_medication", rahul, {"medication_list": ["clarithromycin"]}))

    arjun = build_context("arjun.mehta")
    show("Arjun: active medicines", run_tool("get_active_medications", arjun, {}))
    show("Arjun: check warfarin + aspirin + paracetamol",
         run_tool("drug_interaction_check", arjun, {"medication_list": ["Warfarin", "aspirin", "paracetamol", "Crocin"]}))
    show("Arjun: payment status B8003", run_tool("get_payment_status", arjun, {"invoice_id": "B8003"}))

    show("Rahul tries Arjun's invoice B8003", run_tool("get_payment_status", rahul, {"invoice_id": "B8003"}))
    show("LLM tries to inject patient_id", run_tool("get_lab_results", rahul, {"test_name": "INR", "patient_id": "P1003"}))
    show("Nurse Anita (ortho) views assigned patient P1001",
         run_tool("get_appointments", build_context("nurse.anita", acting_for_patient="P1001"), {}))
    try:
        build_context("nurse.deepak", acting_for_patient="P1001")
    except AuthorizationError as exc:
        print(f"\n=== Nurse Deepak (cardiology) opens P1001\nREFUSED: {exc}")
