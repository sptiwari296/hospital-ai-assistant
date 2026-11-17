"""Tool registry — the list of tools the AI orchestrator (Phase 5) may call.

Each tool has a Pydantic input model. Its JSON schema is what the LLM sees, and every
LLM-proposed call is validated against it before running.

Deliberately NOT here:
  * patient_id — no input model has it; the patient always comes from RequestContext.
  * confirm_action / decline_action — only the USER's explicit yes/no (via the backend)
    may execute a proposed change. The LLM can propose, never approve.
"""
from dataclasses import dataclass
from datetime import date
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.tools import appointments, billing, care, insurance, labs, medications
from app.tools.base import RequestContext, ToolResult


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown fields (e.g. an injected patient_id) are rejected


class NoInput(_In):
    pass


class GetAppointmentsIn(_In):
    include_past: bool = Field(False, description="Also include past and cancelled appointments")


class AvailableSlotsIn(_In):
    appointment_id: str | None = Field(None, description="Appointment to find replacement times for, e.g. A5001")
    doctor_id: str | None = Field(None, description="Or: a specific doctor, e.g. D202")
    date_from: date | None = None
    date_to: date | None = None


class RescheduleIn(_In):
    appointment_id: str
    slot_id: int = Field(description="slot_id taken from get_available_slots")


class CancelIn(_In):
    appointment_id: str


class ProcedureDateIn(_In):
    procedure: str = Field(description="Procedure name or code, e.g. 'knee replacement' or 'TKR'")
    service_date: date | None = Field(None, description="Planned date of the procedure (default today)")


class LabIn(_In):
    test_name: str = Field(description="Test as the patient said it, e.g. 'HbA1c', 'blood sugar'")
    limit: int = Field(5, ge=1, le=20, description="How many results (1 = latest only)")


class LimitIn(_In):
    limit: int = Field(5, ge=1, le=20)


class MedicationListIn(_In):
    medication_list: list[str] = Field(min_length=2, description="Medicine names to check against each other")


class NewMedicationIn(_In):
    medication_list: list[str] = Field(min_length=1, description="New medicine(s) to check against current prescriptions")


class InvoiceIn(_In):
    invoice_id: str


class ProcedureIn(_In):
    procedure: str = Field("knee replacement", description="Procedure name or code")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    func: Callable[..., ToolResult]
    input_model: type[BaseModel]
    kind: Literal["read", "propose_write"]
    description: str


TOOLS: dict[str, ToolSpec] = {t.name: t for t in [
    ToolSpec("get_appointments", appointments.get_appointments, GetAppointmentsIn, "read",
             "List the patient's upcoming appointments (date, time, doctor, department)."),
    ToolSpec("get_available_slots", appointments.get_available_slots, AvailableSlotsIn, "read",
             "Find free slots to replace an appointment, or for a doctor."),
    ToolSpec("reschedule_appointment", appointments.reschedule_appointment, RescheduleIn, "propose_write",
             "PROPOSE moving an appointment to a free slot. Does not change anything; the patient must confirm."),
    ToolSpec("cancel_appointment", appointments.cancel_appointment, CancelIn, "propose_write",
             "PROPOSE cancelling an appointment. Does not change anything; the patient must confirm."),
    ToolSpec("check_insurance_eligibility", insurance.check_insurance_eligibility, ProcedureDateIn, "read",
             "Deterministic insurance check: policy, exclusions, waiting period, limits, pre-authorisation, amounts."),
    ToolSpec("get_lab_results", labs.get_lab_results, LabIn, "read",
             "The patient's lab results for one test (latest first), with unit, reference range and lab flag."),
    ToolSpec("get_radiology_reports", labs.get_radiology_reports, LimitIn, "read",
             "The patient's radiology reports (findings and impression as written)."),
    ToolSpec("drug_interaction_check", medications.drug_interaction_check, MedicationListIn, "read",
             "Check named medicines against each other using the hospital formulary."),
    ToolSpec("get_active_medications", medications.get_active_medications, NoInput, "read",
             "The patient's active prescriptions."),
    ToolSpec("check_new_medication", medications.check_new_medication, NewMedicationIn, "read",
             "Check new medicine(s) against the patient's active prescriptions."),
    ToolSpec("get_bills", billing.get_bills, NoInput, "read", "The patient's bills, balances and overdue status."),
    ToolSpec("get_payment_status", billing.get_payment_status, InvoiceIn, "read", "Payment status of one invoice."),
    ToolSpec("calculate_estimated_bill", billing.calculate_estimated_bill, ProcedureDateIn, "read",
             "Estimated cost of a procedure and the insurance / patient split."),
    ToolSpec("get_pending_orders", care.get_pending_orders, NoInput, "read",
             "Tests, scans and consults ordered for the patient but not completed."),
    ToolSpec("get_preoperative_test_status", care.get_preoperative_test_status, ProcedureIn, "read",
             "Which required pre-operative tests are done, valid for the surgery date, or still outstanding."),
    ToolSpec("get_surgeon_availability", care.get_surgeon_availability, ProcedureIn, "read",
             "The patient's scheduled surgery and the surgeon's next free theatre slots."),
    ToolSpec("get_readiness_checklist", care.get_readiness_checklist, ProcedureIn, "read",
             "Administrative pre-surgery checklist (consent, fasting instructions, escort...)."),
]}


def run_tool(name: str, ctx: RequestContext, arguments: dict) -> ToolResult:
    """Validate arguments against the tool's input model, then run it."""
    spec = TOOLS.get(name)
    if spec is None:
        return ToolResult(tool=name, status="invalid", answer_type="refused", summary=f"Unknown tool '{name}'.")
    try:
        args = spec.input_model.model_validate(arguments)
    except ValidationError as exc:
        return ToolResult(tool=name, status="invalid", answer_type="clarification_needed",
                          summary=f"Invalid input for {name}: {exc.errors()[0]['msg']} ({exc.errors()[0]['loc']})")
    return spec.func(ctx, **args.model_dump())
