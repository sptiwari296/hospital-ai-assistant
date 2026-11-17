"""Shared building blocks for all operational tools.

* RequestContext — WHO is asking. Built by the backend from the login, never by the LLM.
* Authorization  — a patient sees only their own record; staff only assigned patients.
* ToolResult     — one standard result shape for every tool.
* @tool          — turns authorization / input errors into proper refused / clarification results.
"""
import functools
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel

from app.db import connect

ADMIN_ONLY_NOTE = ("This is an administrative status check, not medical clearance. Fitness for surgery is "
                   "decided by your surgeon and anaesthesiologist.")


class AuthorizationError(Exception):
    """The caller may not access this data or perform this action."""


class ToolInputError(Exception):
    """The request is missing information or refers to something unknown."""


@dataclass(frozen=True)
class RequestContext:
    user_id: str
    role: str                 # patient | staff | doc_admin | sys_admin
    patient_id: str | None    # the patient whose data this request may touch
    trace_id: str = ""


def build_context(auth_subject: str, acting_for_patient: str | None = None, trace_id: str = "") -> RequestContext:
    """Called by the backend after login. `acting_for_patient` is only meaningful for staff."""
    with connect() as conn:
        user = conn.execute("SELECT user_id, role, patient_id, is_active FROM users WHERE auth_subject = %s",
                            (auth_subject,)).fetchone()
        if not user or not user["is_active"]:
            raise AuthorizationError("unknown or inactive user")
        if user["role"] == "patient":
            if acting_for_patient and acting_for_patient != user["patient_id"]:
                raise AuthorizationError("patients can only access their own records")
            return RequestContext(user["user_id"], "patient", user["patient_id"], trace_id)
        if user["role"] == "staff" and acting_for_patient:
            assigned = conn.execute("SELECT 1 FROM care_assignments WHERE staff_user_id = %s AND patient_id = %s",
                                    (user["user_id"], acting_for_patient)).fetchone()
            if not assigned:
                raise AuthorizationError("this patient is not assigned to you")
            return RequestContext(user["user_id"], "staff", acting_for_patient, trace_id)
        return RequestContext(user["user_id"], user["role"], None, trace_id)


def require_patient(ctx: RequestContext) -> str:
    if ctx.role not in ("patient", "staff") or not ctx.patient_id:
        raise AuthorizationError("no authorised patient for this request")
    return ctx.patient_id


class ToolResult(BaseModel):
    tool: str
    status: Literal["success", "not_found", "denied", "invalid", "requires_confirmation", "error"]
    answer_type: Literal["calculated", "action_completed", "clarification_needed", "not_found", "refused"]
    summary: str                         # deterministic, human-readable result
    data: dict[str, Any] = {}
    source: str = ""                     # system of record, e.g. "Laboratory Information System"
    references: list[str] = []           # rule ids, document sections, record ids
    notes: list[str] = []                # disclaimers


def tool(name: str, source: str):
    """Decorator: converts expected errors into well-formed ToolResults."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(ctx: RequestContext, *args, **kwargs) -> ToolResult:
            try:
                return fn(ctx, *args, **kwargs)
            except AuthorizationError as exc:
                return ToolResult(tool=name, status="denied", answer_type="refused", source=source,
                                  summary=f"Request refused: {exc}.")
            except ToolInputError as exc:
                return ToolResult(tool=name, status="invalid", answer_type="clarification_needed", source=source,
                                  summary=str(exc))
        inner.tool_name = name
        return inner
    return wrap


# ------------------------------------------------------------------ formatting / privacy helpers
def mask(value: str | None, keep: int = 4) -> str | None:
    """'NWG-2023-114455' -> 'NWG-****-4455'  (sensitive identifiers are never shown in full)."""
    if not value:
        return value
    tail = value[-keep:]
    head = re.sub(r"[A-Za-z0-9]", "*", value[:-keep])
    prefix = value.split("-")[0] if "-" in value else ""
    return (prefix + head[len(prefix):] if prefix else head) + tail


def money(value: Decimal | float | int) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def inr(value: Decimal | float | int) -> str:
    return f"INR {Decimal(value):,.0f}"


def fmt_dt(value) -> str:
    return value.strftime("%d %b %Y, %I:%M %p")


def fmt_date(value) -> str:
    return value.strftime("%d %b %Y")
