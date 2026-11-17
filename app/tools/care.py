"""Tool 6 — Care coordination (EMR orders + pre-op protocol + scheduling).

Supports "Can I proceed with surgery next week?". Every result here is ADMINISTRATIVE:
a complete checklist is never presented as medical clearance — that stays with the clinicians.
"""
from datetime import date, timedelta

from app import clock
from app.db import connect
from app.tools.base import (ADMIN_ONLY_NOTE, RequestContext, ToolResult, fmt_date, fmt_dt, require_patient, tool)
from app.tools.catalog import resolve_procedure

SOURCE = "Electronic Medical Record – care coordination"


def _scheduled_surgery(conn, pid: str, procedure: dict) -> dict | None:
    return conn.execute(
        """SELECT a.appointment_id, s.start_time, d.full_name AS surgeon, d.doctor_id
           FROM appointments a JOIN appointment_slots s USING (slot_id) JOIN doctors d ON d.doctor_id = a.doctor_id
           WHERE a.patient_id = %s AND a.appointment_type = 'surgery' AND a.status = 'scheduled'
             AND a.department = %s AND s.start_time >= %s
           ORDER BY s.start_time LIMIT 1""", (pid, procedure["department"], clock.now())).fetchone()


@tool("get_pending_orders", SOURCE)
def get_pending_orders(ctx: RequestContext) -> ToolResult:
    pid = require_patient(ctx)
    with connect() as conn:
        rows = conn.execute("""SELECT o.order_id, o.item_name, o.order_type, o.status, o.ordered_at,
                                      o.related_procedure_code, d.full_name AS ordered_by
                               FROM medical_orders o JOIN doctors d ON d.doctor_id = o.ordered_by
                               WHERE o.patient_id = %s AND o.status IN ('ordered', 'in_progress')
                               ORDER BY o.ordered_at""", (pid,)).fetchall()
    if not rows:
        return ToolResult(tool="get_pending_orders", status="not_found", answer_type="calculated", source=SOURCE,
                          summary="You have no pending orders.")
    return ToolResult(tool="get_pending_orders", status="success", answer_type="calculated", source=SOURCE,
                      summary="Pending orders:\n" + "\n".join(
                          f"- {r['item_name']} ({r['order_type']}, ordered {fmt_date(r['ordered_at'])} by "
                          f"{r['ordered_by']})" for r in rows),
                      data={"orders": [{**r, "ordered_at": r["ordered_at"].isoformat(timespec="minutes")} for r in rows]},
                      references=[r["order_id"] for r in rows])


def _test_status(conn, pid: str, req: dict, order: dict | None, reference_date: date) -> dict:
    """Status of one pre-op requirement, judged against the SURGERY date (DOC003 validity windows)."""
    item = {"item": req["item_name"], "code": req["item_code"], "required_within_days": req["validity_days"],
            "rule": req["rule"], "source": req["source_document"]}
    if order is None:
        return item | {"status": "not_ordered", "detail": "not yet ordered"}
    if order["status"] != "completed":
        appt = conn.execute(  # e.g. the pre-anaesthesia review may already be booked
            """SELECT s.start_time FROM appointments a JOIN appointment_slots s USING (slot_id)
               WHERE a.patient_id = %s AND a.appointment_type = 'pre_op_review' AND a.status = 'scheduled'
               ORDER BY s.start_time LIMIT 1""", (pid,)).fetchone() if req["item_code"] == "ANESTHESIA_REVIEW" else None
        detail = f"ordered {fmt_date(order['ordered_at'])}, not completed"
        if appt:
            detail += f"; booked for {fmt_dt(appt['start_time'])}"
        return item | {"status": "pending", "detail": detail, "order_id": order["order_id"]}
    done = order["completed_at"].date()
    age = (reference_date - done).days
    if age > req["validity_days"]:
        return item | {"status": "expired", "order_id": order["order_id"],
                       "detail": f"done {fmt_date(done)}, {age} days before surgery (limit {req['validity_days']})"}
    if req["item_code"] == "HBA1C":  # protocol rule: HbA1c must be below 8.0 %
        lab = conn.execute("""SELECT value, result_id FROM lab_results WHERE patient_id = %s AND test_code = 'HBA1C'
                              ORDER BY collected_at DESC LIMIT 1""", (pid,)).fetchone()
        if lab and lab["value"] >= 8:
            return item | {"status": "rule_not_met", "order_id": order["order_id"],
                           "detail": f"HbA1c {float(lab['value']):g} % is not below 8.0 % (result {lab['result_id']})"}
        if lab:
            return item | {"status": "done", "order_id": order["order_id"],
                           "detail": f"done {fmt_date(done)}; HbA1c {float(lab['value']):g} % meets the < 8.0 % rule"}
    return item | {"status": "done", "order_id": order["order_id"], "detail": f"done {fmt_date(done)}"}


@tool("get_preoperative_test_status", SOURCE)
def get_preoperative_test_status(ctx: RequestContext, procedure: str = "knee replacement") -> ToolResult:
    pid = require_patient(ctx)
    with connect() as conn:
        proc = resolve_procedure(conn, procedure)
        surgery = _scheduled_surgery(conn, pid, proc)
        reference_date = surgery["start_time"].date() if surgery else clock.today()
        conditions = conn.execute("SELECT chronic_conditions FROM patients WHERE patient_id = %s",
                                  (pid,)).fetchone()["chronic_conditions"]
        reqs = [r for r in conn.execute("SELECT * FROM preop_requirements WHERE procedure_code = %s ORDER BY item_code",
                                        (proc["procedure_code"],)).fetchall()
                if r["applies_to"] == "all" or r["applies_to"] in conditions]
        orders = {o["item_code"]: o for o in conn.execute(
            """SELECT * FROM medical_orders WHERE patient_id = %s AND status <> 'cancelled'
               ORDER BY ordered_at""", (pid,)).fetchall()}  # latest order per item wins
        items = [_test_status(conn, pid, r, orders.get(r["item_code"]), reference_date) for r in reqs]
    if not reqs:
        return ToolResult(tool="get_preoperative_test_status", status="not_found", answer_type="calculated",
                          source=SOURCE, summary=f"No pre-operative test protocol is defined for {proc['name']}.")
    outstanding = [i for i in items if i["status"] != "done"]
    when = f"surgery on {fmt_dt(surgery['start_time'])}" if surgery else "today (no surgery is scheduled)"
    summary = (f"Pre-operative tests for {proc['name']}, checked against {when}: "
               f"{len(items) - len(outstanding)} of {len(items)} complete.")
    if outstanding:
        summary += "\nStill outstanding:\n" + "\n".join(f"- {i['item']}: {i['detail']}" for i in outstanding)
    return ToolResult(tool="get_preoperative_test_status", status="success", answer_type="calculated", source=SOURCE,
                      summary=summary,
                      data={"procedure": proc["name"], "surgery_date": reference_date.isoformat() if surgery else None,
                            "items": items, "all_complete": not outstanding},
                      references=sorted({i["source"] for i in items}), notes=[ADMIN_ONLY_NOTE])


@tool("get_surgeon_availability", SOURCE)
def get_surgeon_availability(ctx: RequestContext, procedure: str = "knee replacement", limit: int = 3) -> ToolResult:
    pid = require_patient(ctx)
    with connect() as conn:
        proc = resolve_procedure(conn, procedure)
        surgery = _scheduled_surgery(conn, pid, proc)
        surgeon_filter = (surgery["doctor_id"],) if surgery else None
        free = conn.execute(
            """SELECT s.slot_id, s.start_time, d.full_name AS surgeon
               FROM appointment_slots s JOIN doctors d USING (doctor_id)
               WHERE s.slot_type = 'operating_theatre' AND s.is_available AND s.start_time > %s
                 AND d.department = %s AND d.is_surgeon""" + (" AND d.doctor_id = %s" if surgery else "")
            + " ORDER BY s.start_time LIMIT %s",
            (clock.now(), proc["department"], *(surgeon_filter or ()), limit)).fetchall()
    summary = (f"Your {proc['name']} is scheduled for {fmt_dt(surgery['start_time'])} with {surgery['surgeon']}."
               if surgery else f"No {proc['name']} is scheduled for you.")
    if free:
        summary += " Next free operating-theatre slots: " + "; ".join(
            f"{fmt_dt(f['start_time'])} ({f['surgeon']})" for f in free) + "."
    return ToolResult(tool="get_surgeon_availability", status="success", answer_type="calculated",
                      source="Hospital Scheduling System", summary=summary,
                      data={"scheduled_surgery": {**surgery, "start_time": surgery["start_time"].isoformat(timespec="minutes")}
                            if surgery else None,
                            "free_theatre_slots": [{**f, "start_time": f["start_time"].isoformat(timespec="minutes")}
                                                   for f in free]},
                      references=[surgery["appointment_id"]] if surgery else [],
                      notes=["Surgery dates can only be changed by the surgical team."])


@tool("get_readiness_checklist", SOURCE)
def get_readiness_checklist(ctx: RequestContext, procedure: str = "knee replacement") -> ToolResult:
    pid = require_patient(ctx)
    with connect() as conn:
        proc = resolve_procedure(conn, procedure)
        rows = conn.execute("""SELECT item_name, status, updated_at FROM procedure_readiness
                               WHERE patient_id = %s AND procedure_code = %s ORDER BY item_code""",
                            (pid, proc["procedure_code"])).fetchall()
    if not rows:
        return ToolResult(tool="get_readiness_checklist", status="not_found", answer_type="calculated", source=SOURCE,
                          summary=f"No readiness checklist exists for {proc['name']} in your record.")
    pending = [r for r in rows if r["status"] == "pending"]
    summary = f"Readiness checklist for {proc['name']}: {len(rows) - len(pending)} of {len(rows)} items done."
    if pending:
        summary += "\nPending:\n" + "\n".join(f"- {r['item_name']}" for r in pending)
    return ToolResult(tool="get_readiness_checklist", status="success", answer_type="calculated", source=SOURCE,
                      summary=summary,
                      data={"items": [{"item": r["item_name"], "status": r["status"]} for r in rows],
                            "administratively_complete": not pending},
                      references=["DOC003 §5"], notes=[ADMIN_ONLY_NOTE])
