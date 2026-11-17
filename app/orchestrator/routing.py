"""Routing policy — the backend's decision about what may actually run.

The classifier's output is a PROPOSAL. Here we enforce:
  * each intent may only use its allow-listed tools (unknown / off-intent tools are dropped)
  * patient-data intents need an authorised patient in the RequestContext
  * at most MAX_TOOL_CALLS tool calls per request
  * write tools always require confirmation, whatever the classifier said
  * sensible default tools when the classifier proposed none
Every change is recorded in `notes` for the audit trail.
"""
from dataclasses import dataclass, field

from app.orchestrator.classifier import parse_arguments
from app.orchestrator.models import IntentClassification
from app.tools.base import RequestContext
from app.tools.registry import TOOLS

MAX_TOOL_CALLS = 5
READ_TOOLS = {n for n, s in TOOLS.items() if s.kind == "read"}
WRITE_TOOLS = {n for n, s in TOOLS.items() if s.kind == "propose_write"}

INTENT_TOOLS: dict[str, set[str]] = {
    "knowledge_query": set(),
    "appointment_query": {"get_appointments"},
    "appointment_change": {"get_appointments", "get_available_slots", "reschedule_appointment", "cancel_appointment"},
    "insurance_query": {"check_insurance_eligibility", "calculate_estimated_bill"},
    "lab_query": {"get_lab_results", "get_radiology_reports"},
    "medication_query": {"drug_interaction_check", "get_active_medications", "check_new_medication"},
    "billing_query": {"get_bills", "get_payment_status", "calculate_estimated_bill"},
    "care_query": {"get_pending_orders", "get_preoperative_test_status", "get_surgeon_availability",
                   "get_readiness_checklist"},
    "mixed": READ_TOOLS,
    "surgery_readiness": set(),   # fixed plan in workflows.py
}
DEFAULT_TOOL = {"appointment_query": "get_appointments", "billing_query": "get_bills",
                "medication_query": "get_active_medications", "care_query": "get_pending_orders"}
NO_PATIENT_NEEDED = {"drug_interaction_check"}
RAG_ALLOWED = {"knowledge_query", "mixed", "insurance_query", "care_query", "billing_query", "medication_query",
               "lab_query", "appointment_query"}
FIXED_RESPONSE_INTENTS = {"greeting", "medical_advice", "other_patient", "emergency", "out_of_scope"}


@dataclass
class RoutePlan:
    intent: str
    run_rag: bool = False
    rag_query: str = ""
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    requires_confirmation: bool = False
    notes: list[str] = field(default_factory=list)
    refusal: str | None = None
    clarification: str | None = None


def validate_route(cls: IntentClassification, ctx: RequestContext, message: str) -> RoutePlan:
    plan = RoutePlan(intent=cls.intent)
    if cls.intent in FIXED_RESPONSE_INTENTS:
        return plan

    allowed = INTENT_TOOLS.get(cls.intent, set())
    for call in cls.tool_calls:
        if call.tool not in TOOLS:
            plan.notes.append(f"dropped unknown tool '{call.tool}'")
        elif call.tool not in allowed:
            plan.notes.append(f"dropped '{call.tool}': not allowed for intent {cls.intent}")
        elif len(plan.tool_calls) >= MAX_TOOL_CALLS:
            plan.notes.append(f"dropped '{call.tool}': more than {MAX_TOOL_CALLS} tool calls")
        else:
            plan.tool_calls.append((call.tool, parse_arguments(call)))

    if not plan.tool_calls and cls.intent in DEFAULT_TOOL:
        plan.tool_calls.append((DEFAULT_TOOL[cls.intent], {}))
        plan.notes.append(f"added default tool {DEFAULT_TOOL[cls.intent]}")
    if not plan.tool_calls and cls.intent == "insurance_query" and cls.procedure.strip():
        plan.tool_calls.append(("check_insurance_eligibility", {"procedure": cls.procedure.strip()}))
        plan.notes.append("added check_insurance_eligibility for the named procedure (no date needed)")

    # Permissions come from the login, not from the classifier's requires_authentication flag.
    needs_patient = cls.intent in ("appointment_change", "surgery_readiness") or any(
        t not in NO_PATIENT_NEEDED for t, _ in plan.tool_calls)
    if needs_patient and not ctx.patient_id:
        if ctx.role == "staff":
            plan.clarification = "Please select one of your assigned patients first."
        else:
            plan.refusal = "Your account cannot access patient records."
        return plan

    if any(t in WRITE_TOOLS for t, _ in plan.tool_calls):
        if not cls.requires_confirmation:
            plan.notes.append("forced requires_confirmation=true for a write action")
        plan.requires_confirmation = True

    if cls.requires_rag and cls.intent in RAG_ALLOWED or cls.intent == "knowledge_query":
        plan.run_rag = True
        plan.rag_query = cls.rag_query.strip() or message

    if not plan.tool_calls and not plan.run_rag and cls.intent not in ("appointment_change", "surgery_readiness"):
        plan.clarification = cls.clarification_question or "Could you tell me a little more about what you need?"
    return plan
