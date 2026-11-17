"""Patient-data endpoints for the dashboard. Every one runs the SAME authorised tools as the chat."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.auth import CurrentUser, current_user
from app.db import connect
from app.tools import appointments, billing, insurance, labs
from app.tools.base import AuthorizationError, RequestContext, ToolResult, build_context, mask, require_patient

router = APIRouter(prefix="/api")
STATUS_CODES = {"denied": 403, "invalid": 400}


def patient_context(user: CurrentUser = Depends(current_user),
                    patient_id: str | None = Query(None, description="Staff only: an assigned patient")) -> RequestContext:
    try:
        ctx = build_context(user.auth_subject, acting_for_patient=patient_id if user.role == "staff" else None)
        require_patient(ctx)
        return ctx
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from None


def respond(result: ToolResult) -> ToolResult:
    if result.status in STATUS_CODES:
        raise HTTPException(STATUS_CODES[result.status], result.summary)
    return result


@router.get("/patient/profile")
def profile(ctx: RequestContext = Depends(patient_context)):
    with connect() as conn:
        p = conn.execute("SELECT * FROM patients WHERE patient_id = %s", (ctx.patient_id,)).fetchone()
        policy = conn.execute(
            """SELECT pl.provider, pl.plan_name, p.policy_number, p.valid_to, p.status
               FROM insurance_policies p JOIN insurance_plans pl USING (plan_id)
               WHERE p.patient_id = %s ORDER BY p.valid_to DESC LIMIT 1""", (ctx.patient_id,)).fetchone()
    return {"patient_id": p["patient_id"], "full_name": p["full_name"], "dob": p["dob"], "gender": p["gender"],
            "blood_group": p["blood_group"], "allergies": p["allergies"],
            "phone": mask(p["phone"]), "email": mask(p["email"], keep=12), "national_id": mask(p["national_id"]),
            "insurance": {**policy, "policy_number": mask(policy["policy_number"], keep_prefix=True)} if policy else None}


@router.get("/appointments", response_model=ToolResult)
def list_appointments(include_past: bool = False, ctx: RequestContext = Depends(patient_context)):
    return respond(appointments.get_appointments(ctx, include_past=include_past))


@router.get("/appointments/{appointment_id}/slots", response_model=ToolResult)
def appointment_slots(appointment_id: str, ctx: RequestContext = Depends(patient_context)):
    return respond(appointments.get_available_slots(ctx, appointment_id=appointment_id))


class RescheduleRequest(BaseModel):
    appointment_id: str
    slot_id: int


class CancelRequest(BaseModel):
    appointment_id: str


@router.post("/appointments/reschedule", response_model=ToolResult)
def reschedule(body: RescheduleRequest, ctx: RequestContext = Depends(patient_context)):
    """Only PROPOSES the change; confirm with POST /api/actions/{confirmation_id}/confirm."""
    return respond(appointments.reschedule_appointment(ctx, body.appointment_id, body.slot_id))


@router.post("/appointments/cancel", response_model=ToolResult)
def cancel(body: CancelRequest, ctx: RequestContext = Depends(patient_context)):
    return respond(appointments.cancel_appointment(ctx, body.appointment_id))


@router.post("/actions/{confirmation_id}/confirm", response_model=ToolResult)
def confirm(confirmation_id: str, ctx: RequestContext = Depends(patient_context)):
    return respond(appointments.confirm_action(ctx, confirmation_id))


@router.post("/actions/{confirmation_id}/decline", response_model=ToolResult)
def decline(confirmation_id: str, ctx: RequestContext = Depends(patient_context)):
    return respond(appointments.decline_action(ctx, confirmation_id))


@router.get("/labs")
def lab_results(test: str | None = None, limit: int = Query(5, ge=1, le=20),
                ctx: RequestContext = Depends(patient_context)):
    if test:
        return respond(labs.get_lab_results(ctx, test, limit))
    with connect() as conn:  # dashboard overview: latest result of each test
        rows = conn.execute(
            """SELECT DISTINCT ON (test_code) result_id, test_code, test_name, value, unit, ref_low, ref_high, flag,
                      collected_at FROM lab_results WHERE patient_id = %s
               ORDER BY test_code, collected_at DESC""", (ctx.patient_id,)).fetchall()
    return {"latest_by_test": [{**r, "value": float(r["value"]),
                                "reference_range": f"{float(r['ref_low']):g}–{float(r['ref_high']):g} {r['unit']}"}
                               for r in sorted(rows, key=lambda r: r["collected_at"], reverse=True)],
            "source": labs.LIS, "note": labs.NO_DIAGNOSIS_NOTE}


class InsuranceRequest(BaseModel):
    procedure: str
    service_date: date | None = None


@router.post("/insurance/verify", response_model=ToolResult)
def verify_insurance(body: InsuranceRequest, ctx: RequestContext = Depends(patient_context)):
    return respond(insurance.check_insurance_eligibility(ctx, body.procedure, body.service_date))


@router.get("/bills", response_model=ToolResult)
def bills(ctx: RequestContext = Depends(patient_context)):
    return respond(billing.get_bills(ctx))
