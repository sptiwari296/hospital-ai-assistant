"""Structured multi-step workflows (fixed plans, not free LLM reasoning).

Each step runs a validated tool through `Executor`, which records an auditable StepRecord.
"""
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from app.db import connect
from app.orchestrator.models import Session, StepRecord
from app.tools.base import ADMIN_ONLY_NOTE, ToolResult, fmt_dt
from app.tools.registry import run_tool


@dataclass
class Part:
    """One piece of the final answer."""
    answer_type: str
    text: str
    sources: list[str] = field(default_factory=list)     # system names
    references: list[str] = field(default_factory=list)  # e.g. "DOC003 §4.1"
    citations: list[dict] = field(default_factory=list)  # document citations (from RAG)
    conflicts: list[dict] = field(default_factory=list)
    escalation: dict | None = None
    confirmation: dict | None = None


class Executor:
    """Runs tools one at a time and keeps the plan record."""

    def __init__(self, session: Session):
        self.session = session
        self.steps: list[StepRecord] = []
        self.rag_model: str | None = None

    def run(self, tool: str, args: dict) -> ToolResult:
        started = time.perf_counter()
        result = run_tool(tool, self.session.ctx, args)
        self.steps.append(StepRecord(step=len(self.steps) + 1, name=tool, arguments=args, status=result.status,
                                     answer_type=result.answer_type,
                                     latency_ms=int((time.perf_counter() - started) * 1000),
                                     summary=result.summary[:200]))
        return result


def part_from(result: ToolResult) -> Part:
    text = result.summary + ("\n" + "\n".join(f"Note: {n}" for n in result.notes) if result.notes else "")
    return Part(result.answer_type, text, [result.source] if result.source else [], result.references)


# ---------------------------------------------------------------- appointment change
ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
            "fifth": 5, "5th": 5, "sixth": 6, "6th": 6, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
OPTION = re.compile(r"^\s*(?:i'?ll take |let'?s do |)(?:the |option |number |no\.? |#)?\s*"
                    r"(\d{1,2}|" + "|".join(ORDINALS) + r")(?: one| option)?\s*(?:please)?[.!)]?\s*$", re.IGNORECASE)


def pick_offered_option(message: str, session: Session) -> int | None:
    """'2', 'option 2', 'the first one' -> slot_id of that offered slot (deterministic, no LLM)."""
    m = OPTION.match(message)
    if not (m and session.offered_slots):
        return None
    token = m.group(1).lower()
    index = (int(token) if token.isdigit() else ORDINALS[token]) - 1
    slots = session.offered_slots["slots"]
    return slots[index]["slot_id"] if 0 <= index < len(slots) else None


def _matches(appt: dict, hint: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9]+", hint.lower()) if len(w) > 2 and w not in
             {"the", "appointment", "appointments", "my", "with", "reschedule", "cancel", "move", "change"}]
    haystack = " ".join(str(appt[k]) for k in ("department", "doctor", "type", "reason", "display_time")).lower()
    return bool(words) and all(w in haystack for w in words)


def appointment_change(ex: Executor, message: str, tool_calls: list[tuple[str, dict]], hint: str) -> Part:
    session = ex.session
    proposals = [(t, a) for t, a in tool_calls if t in ("reschedule_appointment", "cancel_appointment")]

    # A concrete proposal: the slot MUST be one we offered for that appointment.
    for tool, args in proposals:
        if tool == "reschedule_appointment":
            offered = session.offered_slots or {}
            offered_ids = {s["slot_id"] for s in offered.get("slots", [])}
            if args.get("appointment_id") != offered.get("appointment_id") or args.get("slot_id") not in offered_ids:
                return Part("clarification_needed", "Please choose one of the times I offered (reply with the "
                            "option number), or tell me which appointment you want to move.",
                            ["Hospital Scheduling System"])
        result = ex.run(tool, args)
        return _proposal_part(session, result)

    wants_cancel = "cancel" in message.lower()
    upcoming = ex.run("get_appointments", {})
    if upcoming.status != "success":
        return part_from(upcoming)
    candidates = [a for a in upcoming.data["appointments"] if _matches(a, hint or message)]
    if len(candidates) != 1:
        listing = "\n".join(f"- {a['display_time']} – {a['doctor']} ({a['department']})"
                            for a in upcoming.data["appointments"])
        return Part("clarification_needed", f"Which appointment do you want to {'cancel' if wants_cancel else 'change'}?\n"
                    + listing, ["Hospital Scheduling System"])
    target = candidates[0]
    if wants_cancel:
        return _proposal_part(session, ex.run("cancel_appointment", {"appointment_id": target["appointment_id"]}))

    slots = ex.run("get_available_slots", {"appointment_id": target["appointment_id"]})
    if slots.status != "success":
        return part_from(slots)
    session.offered_slots = {"appointment_id": target["appointment_id"], "slots": slots.data["slots"]}
    options = "\n".join(f"{i}. {s['display_time']} – {s['doctor']}" for i, s in enumerate(slots.data["slots"], 1))
    return Part("clarification_needed",
                f"Your {target['department']} appointment is on {target['display_time']} with {target['doctor']}. "
                f"These times are available:\n{options}\nReply with the option number you prefer.",
                [slots.source], [target["appointment_id"]])


def _proposal_part(session: Session, result: ToolResult) -> Part:
    if result.status != "requires_confirmation":
        return part_from(result)
    session.offered_slots = None
    session.pending_confirmation = {"confirmation_id": result.data["confirmation_id"], "summary": result.summary}
    return Part("clarification_needed", result.summary, [result.source],
                confirmation={"confirmation_id": result.data["confirmation_id"]})


# ---------------------------------------------------------------- surgery readiness (PDF multi-step scenario)
def _planned_procedure(session: Session) -> str | None:
    with connect() as conn:
        row = conn.execute("""SELECT item_code FROM medical_orders WHERE patient_id = %s AND order_type = 'procedure'
                              AND status IN ('ordered', 'in_progress') ORDER BY ordered_at DESC LIMIT 1""",
                           (session.ctx.patient_id,)).fetchone()
    return row["item_code"] if row else None


def surgery_readiness(ex: Executor, procedure_hint: str) -> Part:
    procedure = procedure_hint.strip() or _planned_procedure(ex.session)
    if not procedure:
        return Part("clarification_needed", "I could not find a planned surgery in your record. Which procedure "
                    "do you mean?", ["Electronic Medical Record – care coordination"])

    # Step order follows data dependencies: the surgery date is needed to judge test validity and insurance.
    surgeon = ex.run("get_surgeon_availability", {"procedure": procedure})            # PDF step 4
    if surgeon.status not in ("success",):
        return part_from(surgeon)
    scheduled = surgeon.data.get("scheduled_surgery")
    tests = ex.run("get_preoperative_test_status", {"procedure": procedure})          # PDF steps 1–2
    service_date = scheduled["start_time"][:10] if scheduled else None
    insurance = ex.run("check_insurance_eligibility",                                  # PDF step 3
                       {"procedure": procedure, "service_date": service_date})
    checklist = ex.run("get_readiness_checklist", {"procedure": procedure})            # PDF step 5

    # PDF step 6: combine and identify unresolved items (deterministic)
    done, open_items = [], []
    if scheduled:
        done.append(f"Surgery booked: {fmt_dt_iso(scheduled['start_time'])} with {scheduled['surgeon']}")
    else:
        open_items.append("No surgery date is scheduled yet")
    for item in tests.data.get("items", []):
        (done if item["status"] == "done" else open_items).append(f"{item['item']}: {item['detail']}")
    status = insurance.data.get("eligibility_status")
    if status == "eligible":
        done.append("Insurance: eligible" + (" (pre-authorisation approved)" if insurance.data.get("pre_authorization")
                                             else ""))
    elif status:
        label = {"requires_pre_authorization": "pre-authorisation needed", "not_covered": "not covered",
                 "not_eligible": "not eligible", "no_active_policy": "no active policy",
                 "manual_review": "needs review by the Insurance Desk"}.get(status, status)
        open_items.append(f"Insurance: {label} – {insurance.data['reasons'][0]}")
    for item in checklist.data.get("items", []):
        (done if item["status"] == "done" else open_items).append(f"Checklist: {item['item']}")

    # PDF step 7: grounded response + escalate the clinical decision
    name = tests.data.get("procedure", procedure)
    text = f"Administrative status for your {name}:\n"
    text += "Done:\n" + ("\n".join(f"- {d}" for d in done) or "- nothing yet") + "\n"
    text += (f"Still open ({len(open_items)}):\n" + "\n".join(f"- {o}" for o in open_items)) if open_items \
        else "No open administrative items."
    text += f"\n{ADMIN_ONLY_NOTE}"
    refs = sorted({*surgeon.references, *tests.references, *insurance.references, *checklist.references})
    sources = sorted({r.source for r in (surgeon, tests, insurance, checklist) if r.source})
    return Part("calculated", text, sources, refs,
                escalation={"required": True, "to": ["operating surgeon", "anaesthesiologist"],
                            "reason": "Clinical fitness for surgery is a clinician's decision.",
                            "open_items": len(open_items)})


def fmt_dt_iso(value: str) -> str:
    return fmt_dt(datetime.fromisoformat(value))
