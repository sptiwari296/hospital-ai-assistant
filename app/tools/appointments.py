"""Tool 1 — Appointment management (Appointment Management System).

Workflow for changes (never claims success before the database confirms it):
  1 verify identity/permissions (RequestContext)      2 load the patient's own appointment
  3 check the requested slot                          4 offer alternatives (get_available_slots)
  5 PROPOSE: store a pending action, ask the user     6 on confirm_action: execute in one locked transaction
  7 re-read the appointment to verify the change      8 return action_completed only if verified
"""
import json
import uuid
from datetime import date, datetime, time, timedelta

import psycopg

from app import clock
from app.db import connect
from app.tools.base import (AuthorizationError, RequestContext, ToolInputError, ToolResult, fmt_dt,
                            require_patient, tool)

SOURCE = "Hospital Scheduling System"
MIN_HOURS_BEFORE_CHANGE = 24          # changes must be made at least 24 h before the appointment
CONFIRMATION_TTL_MINUTES = 10
PATIENT_CHANGEABLE = {"consultation", "follow_up", "imaging", "pre_op_review"}  # surgery: surgical team only

APPT_SQL = """
SELECT a.appointment_id, a.patient_id, a.status, a.appointment_type, a.department, a.reason, a.doctor_id,
       d.full_name AS doctor, s.slot_id, s.start_time, s.duration_min, s.slot_type
FROM appointments a
JOIN appointment_slots s USING (slot_id)
JOIN doctors d ON d.doctor_id = a.doctor_id
"""


def _public(row: dict) -> dict:
    return {"appointment_id": row["appointment_id"], "date_time": row["start_time"].isoformat(timespec="minutes"),
            "display_time": fmt_dt(row["start_time"]), "doctor": row["doctor"], "department": row["department"],
            "type": row["appointment_type"], "reason": row["reason"], "status": row["status"]}


def _own_appointment(conn, pid: str, appointment_id: str, lock: bool = False) -> dict:
    row = conn.execute(APPT_SQL + " WHERE a.appointment_id = %s AND a.patient_id = %s"
                       + (" FOR UPDATE OF a" if lock else ""), (appointment_id, pid)).fetchone()
    if not row:  # same message whether it doesn't exist or belongs to someone else (no information leak)
        raise ToolInputError(f"Appointment {appointment_id} was not found in your records.")
    return row


def _check_changeable(appt: dict) -> None:
    if appt["status"] != "scheduled":
        raise ToolInputError(f"Appointment {appt['appointment_id']} is {appt['status']} and cannot be changed.")
    if appt["appointment_type"] not in PATIENT_CHANGEABLE:
        raise ToolInputError("Surgery dates can only be changed by the surgical team. Please contact the "
                             f"{appt['department']} department.")
    if appt["start_time"] - clock.now() < timedelta(hours=MIN_HOURS_BEFORE_CHANGE):
        raise ToolInputError(f"Appointments can only be changed at least {MIN_HOURS_BEFORE_CHANGE} hours in advance. "
                             f"Please call the {appt['department']} department.")


def _check_new_slot(conn, appt: dict, slot_id: int, lock: bool = False) -> dict:
    slot = conn.execute(
        """SELECT s.slot_id, s.doctor_id, s.start_time, s.slot_type, s.is_available, d.full_name AS doctor, d.department
           FROM appointment_slots s JOIN doctors d USING (doctor_id) WHERE s.slot_id = %s"""
        + (" FOR UPDATE OF s" if lock else ""), (slot_id,)).fetchone()
    if not slot:
        raise ToolInputError(f"Slot {slot_id} does not exist.")
    if slot["slot_id"] == appt["slot_id"]:
        raise ToolInputError("That is already the time of this appointment.")
    if not slot["is_available"] or slot["start_time"].date() <= clock.today():
        raise ToolInputError(f"Slot {slot_id} is no longer available. Please choose another time.")
    if slot["department"] != appt["department"] or slot["slot_type"] != appt["slot_type"]:
        raise ToolInputError(f"Slot {slot_id} is not a matching {appt['department']} {appt['slot_type']} slot.")
    return slot


# ------------------------------------------------------------------ read tools
@tool("get_appointments", SOURCE)
def get_appointments(ctx: RequestContext, include_past: bool = False) -> ToolResult:
    pid = require_patient(ctx)
    with connect(pid) as conn:
        rows = conn.execute(APPT_SQL + " WHERE a.patient_id = %s"
                            + ("" if include_past else " AND a.status = 'scheduled' AND s.start_time >= %s")
                            + " ORDER BY s.start_time", (pid,) if include_past else (pid, clock.now())).fetchall()
    whose = "Your" if ctx.role == "patient" else f"Patient {pid}'s"
    if not rows:
        return ToolResult(tool="get_appointments", status="not_found", answer_type="calculated", source=SOURCE,
                          summary=f"{whose} record has no upcoming appointments.")
    lines = [f"{r['display_time']} – {r['doctor']} ({r['department']}, {r['type']})" for r in map(_public, rows)]
    return ToolResult(tool="get_appointments", status="success", answer_type="calculated", source=SOURCE,
                      summary=f"{whose} {'appointments' if include_past else 'upcoming appointments'}:\n"
                      + "\n".join(lines),
                      data={"appointments": [_public(r) for r in rows]},
                      references=[r["appointment_id"] for r in rows])


@tool("get_available_slots", SOURCE)
def get_available_slots(ctx: RequestContext, appointment_id: str | None = None, doctor_id: str | None = None,
                        date_from: date | None = None, date_to: date | None = None, limit: int = 6) -> ToolResult:
    """Free slots that could replace `appointment_id` (same department and slot type), or for one doctor."""
    pid = require_patient(ctx)
    start = datetime.combine(clock.today() + timedelta(days=1), time.min)  # replacement slots start tomorrow
    if date_from:
        start = max(start, datetime.combine(date_from, time.min))
    end_day = date_to or (start.date() + timedelta(days=14))
    with connect(pid) as conn:
        if appointment_id:
            appt = _own_appointment(conn, pid, appointment_id)
            _check_changeable(appt)
            # same doctor first, then other doctors of the same department
            rows = conn.execute(
                """SELECT s.slot_id, s.start_time, s.doctor_id, d.full_name AS doctor, d.department
                   FROM appointment_slots s JOIN doctors d USING (doctor_id)
                   WHERE d.department = %s AND s.slot_type = %s AND s.is_available
                     AND s.start_time > %s AND s.start_time::date <= %s
                   ORDER BY (s.doctor_id <> %s), s.start_time LIMIT %s""",
                (appt["department"], appt["slot_type"], start, end_day, appt["doctor_id"], limit)).fetchall()
        elif doctor_id:
            rows = conn.execute(
                """SELECT s.slot_id, s.start_time, s.doctor_id, d.full_name AS doctor, d.department
                   FROM appointment_slots s JOIN doctors d USING (doctor_id)
                   WHERE s.doctor_id = %s AND s.slot_type <> 'operating_theatre' AND s.is_available
                     AND s.start_time > %s AND s.start_time::date <= %s
                   ORDER BY s.start_time LIMIT %s""", (doctor_id, start, end_day, limit)).fetchall()
        else:
            raise ToolInputError("Which appointment (or doctor) should I find free slots for?")
    if not rows:
        return ToolResult(tool="get_available_slots", status="not_found", answer_type="calculated", source=SOURCE,
                          summary="No free slots were found in that period. Try a later date range.")
    slots = [{"slot_id": r["slot_id"], "date_time": r["start_time"].isoformat(timespec="minutes"),
              "display_time": fmt_dt(r["start_time"]), "doctor": r["doctor"], "department": r["department"]}
             for r in rows]
    return ToolResult(tool="get_available_slots", status="success", answer_type="calculated", source=SOURCE,
                      summary="Available times:\n" + "\n".join(
                          f"[slot {s['slot_id']}] {s['display_time']} – {s['doctor']}" for s in slots),
                      data={"for_appointment": appointment_id, "slots": slots})


# ------------------------------------------------------------------ write tools: step 5 (propose)
def _propose(conn, ctx: RequestContext, pid: str, action: str, params: dict, summary: str) -> ToolResult:
    confirmation_id = f"CNF-{uuid.uuid4().hex[:10].upper()}"
    now = clock.now()
    conn.execute(
        """INSERT INTO pending_actions (confirmation_id, trace_id, user_id, patient_id, action, params, summary,
                                        created_at, expires_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (confirmation_id, ctx.trace_id, ctx.user_id, pid, action, json.dumps(params), summary, now,
         now + timedelta(minutes=CONFIRMATION_TTL_MINUTES)))
    conn.commit()
    return ToolResult(tool=action, status="requires_confirmation", answer_type="clarification_needed", source=SOURCE,
                      summary=f"{summary} Shall I go ahead? (Nothing has been changed yet.)",
                      data={"confirmation_id": confirmation_id, "expires_in_minutes": CONFIRMATION_TTL_MINUTES,
                            **params})


@tool("reschedule_appointment", SOURCE)
def reschedule_appointment(ctx: RequestContext, appointment_id: str, slot_id: int) -> ToolResult:
    pid = require_patient(ctx)
    with connect(pid) as conn:
        appt = _own_appointment(conn, pid, appointment_id)
        _check_changeable(appt)
        slot = _check_new_slot(conn, appt, slot_id)
        summary = (f"Move your {appt['department']} appointment from {fmt_dt(appt['start_time'])} "
                   f"({appt['doctor']}) to {fmt_dt(slot['start_time'])} ({slot['doctor']}).")
        return _propose(conn, ctx, pid, "reschedule_appointment",
                        {"appointment_id": appointment_id, "slot_id": slot_id}, summary)


@tool("cancel_appointment", SOURCE)
def cancel_appointment(ctx: RequestContext, appointment_id: str) -> ToolResult:
    pid = require_patient(ctx)
    with connect(pid) as conn:
        appt = _own_appointment(conn, pid, appointment_id)
        _check_changeable(appt)
        summary = f"Cancel your {appt['department']} appointment on {fmt_dt(appt['start_time'])} with {appt['doctor']}."
        return _propose(conn, ctx, pid, "cancel_appointment", {"appointment_id": appointment_id}, summary)


# ------------------------------------------------------------------ steps 6–8 (execute + verify)
def _load_pending(conn, ctx: RequestContext, confirmation_id: str) -> dict:
    row = conn.execute("SELECT * FROM pending_actions WHERE confirmation_id = %s FOR UPDATE",
                       (confirmation_id,)).fetchone()
    if not row or row["user_id"] != ctx.user_id or row["patient_id"] != ctx.patient_id:
        raise AuthorizationError("this confirmation does not belong to you")
    if row["status"] != "pending":
        raise ToolInputError(f"This request was already {row['status']}.")
    if row["expires_at"] < clock.now():
        conn.execute("UPDATE pending_actions SET status = 'expired' WHERE confirmation_id = %s", (confirmation_id,))
        conn.commit()
        raise ToolInputError("This confirmation has expired. Please ask again.")
    return row


@tool("confirm_action", SOURCE)
def confirm_action(ctx: RequestContext, confirmation_id: str) -> ToolResult:
    """Execute a previously proposed change. Called ONLY after the user explicitly says yes."""
    pid = require_patient(ctx)
    with connect(pid) as conn:
        pending = _load_pending(conn, ctx, confirmation_id)
        params, action = pending["params"], pending["action"]
        try:
            with conn.transaction():  # 6. execute: re-check everything under row locks
                appt = _own_appointment(conn, pid, params["appointment_id"], lock=True)
                _check_changeable(appt)
                if action == "reschedule_appointment":
                    slot = _check_new_slot(conn, appt, params["slot_id"], lock=True)
                    conn.execute("UPDATE appointment_slots SET is_available = true WHERE slot_id = %s", (appt["slot_id"],))
                    conn.execute("UPDATE appointment_slots SET is_available = false WHERE slot_id = %s", (slot["slot_id"],))
                    conn.execute("UPDATE appointments SET slot_id = %s, doctor_id = %s, updated_at = %s "
                                 "WHERE appointment_id = %s",
                                 (slot["slot_id"], slot["doctor_id"], clock.now(), appt["appointment_id"]))
                else:
                    conn.execute("UPDATE appointments SET status = 'cancelled', updated_at = %s WHERE appointment_id = %s",
                                 (clock.now(), appt["appointment_id"]))
                    conn.execute("UPDATE appointment_slots SET is_available = true WHERE slot_id = %s", (appt["slot_id"],))
        except (ToolInputError, psycopg.Error) as exc:
            conn.rollback()
            reason = str(exc) if isinstance(exc, ToolInputError) else "the scheduling system rejected the change"
            conn.execute("UPDATE pending_actions SET status = 'failed', executed_at = %s, result = %s "
                         "WHERE confirmation_id = %s", (clock.now(), json.dumps({"error": reason}), confirmation_id))
            conn.commit()
            return ToolResult(tool="confirm_action", status="error", answer_type="refused", source=SOURCE,
                              summary=f"The change was NOT made: {reason}")

        # 7. verify in the source system with a fresh read
        after = _own_appointment(conn, pid, params["appointment_id"])
        verified = (after["status"] == "scheduled" and after["slot_id"] == params["slot_id"]) \
            if action == "reschedule_appointment" else after["status"] == "cancelled"
        result = _public(after)
        conn.execute("UPDATE pending_actions SET status = %s, executed_at = %s, result = %s WHERE confirmation_id = %s",
                     ("executed" if verified else "failed", clock.now(), json.dumps(result), confirmation_id))
        conn.commit()

    if not verified:  # 8. never claim success that the system did not confirm
        return ToolResult(tool="confirm_action", status="error", answer_type="refused", source=SOURCE,
                          summary="The change could not be verified in the scheduling system. Please contact the "
                                  "front desk.", data={"appointment": result})
    done = (f"Done: your appointment is now on {result['display_time']} with {result['doctor']}."
            if action == "reschedule_appointment" else f"Done: your appointment on {result['display_time']} is cancelled.")
    return ToolResult(tool="confirm_action", status="success", answer_type="action_completed", source=SOURCE,
                      summary=done + " (verified in the scheduling system)", data={"appointment": result},
                      references=[confirmation_id, params["appointment_id"]])


@tool("decline_action", SOURCE)
def decline_action(ctx: RequestContext, confirmation_id: str) -> ToolResult:
    pid = require_patient(ctx)
    with connect(pid) as conn:
        _load_pending(conn, ctx, confirmation_id)
        conn.execute("UPDATE pending_actions SET status = 'declined', executed_at = %s WHERE confirmation_id = %s",
                     (clock.now(), confirmation_id))
        conn.commit()
    return ToolResult(tool="decline_action", status="success", answer_type="action_completed", source=SOURCE,
                      summary="OK, nothing was changed.")
