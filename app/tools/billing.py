"""Tool 5 — Billing (Billing System). All money maths is Decimal arithmetic in code."""
from datetime import date
from decimal import Decimal

from app import clock
from app.db import connect
from app.tools.base import (RequestContext, ToolInputError, ToolResult, fmt_date, inr, money, require_patient,
                            tool)
from app.tools.catalog import resolve_procedure
from app.tools.insurance import default_service_date, evaluate_eligibility

SOURCE = "Hospital Billing System"


def _bill_view(b: dict, items: list[dict]) -> dict:
    balance = money(b["patient_payable"] - b["amount_paid"])
    overdue = balance > 0 and b["due_date"] < clock.today()
    return {"invoice_id": b["bill_id"], "description": b["description"], "issued": b["issued_at"].isoformat(),
            "due": b["due_date"].isoformat(), "total": money(b["total_amount"]),
            "insurance_covered": money(b["insurance_covered"]), "patient_payable": money(b["patient_payable"]),
            "amount_paid": money(b["amount_paid"]), "balance_due": balance, "status": b["status"], "overdue": overdue,
            "items": [{"description": i["description"], "amount": money(i["amount"])} for i in items]}


def _bills(conn, pid: str, invoice_id: str | None = None) -> list[dict]:
    rows = conn.execute("SELECT * FROM bills WHERE patient_id = %s" + (" AND bill_id = %s" if invoice_id else "")
                        + " ORDER BY issued_at DESC", (pid, invoice_id) if invoice_id else (pid,)).fetchall()
    return [_bill_view(b, conn.execute("SELECT * FROM bill_items WHERE bill_id = %s ORDER BY item_id",
                                       (b["bill_id"],)).fetchall()) for b in rows]


@tool("get_bills", SOURCE)
def get_bills(ctx: RequestContext) -> ToolResult:
    pid = require_patient(ctx)
    with connect() as conn:
        bills = _bills(conn, pid)
    if not bills:
        return ToolResult(tool="get_bills", status="not_found", answer_type="not_found", source=SOURCE,
                          summary="You have no bills on record.")
    outstanding = money(sum((b["balance_due"] for b in bills), Decimal(0)))
    lines = [f"{b['invoice_id']} {b['description']}: total {inr(b['total'])}, insurance {inr(b['insurance_covered'])}, "
             f"you owe {inr(b['balance_due'])} ({b['status']}{', OVERDUE' if b['overdue'] else ''})" for b in bills]
    return ToolResult(tool="get_bills", status="success", answer_type="calculated", source=SOURCE,
                      summary=f"Total outstanding: {inr(outstanding)}.\n" + "\n".join(lines),
                      data={"bills": bills, "total_outstanding": outstanding},
                      references=[b["invoice_id"] for b in bills])


@tool("get_payment_status", SOURCE)
def get_payment_status(ctx: RequestContext, invoice_id: str) -> ToolResult:
    pid = require_patient(ctx)
    with connect() as conn:
        bills = _bills(conn, pid, invoice_id.strip().upper())
    if not bills:  # not found and not-yours look the same
        raise ToolInputError(f"Invoice {invoice_id} was not found in your records.")
    b = bills[0]
    summary = (f"Invoice {b['invoice_id']} ({b['description']}): you paid {inr(b['amount_paid'])} of "
               f"{inr(b['patient_payable'])}; balance {inr(b['balance_due'])}, status {b['status']}")
    summary += f", overdue since {fmt_date(date.fromisoformat(b['due']))}." if b["overdue"] else f", due {fmt_date(date.fromisoformat(b['due']))}."
    return ToolResult(tool="get_payment_status", status="success", answer_type="calculated", source=SOURCE,
                      summary=summary, data=b, references=[b["invoice_id"]])


@tool("calculate_estimated_bill", SOURCE)
def calculate_estimated_bill(ctx: RequestContext, procedure: str, service_date: date | None = None) -> ToolResult:
    pid = require_patient(ctx)
    with connect() as conn:
        proc = resolve_procedure(conn, procedure)
        service_date = service_date or default_service_date(conn, pid, proc)
        elig = evaluate_eligibility(conn, pid, proc, service_date)
    status = elig["eligibility_status"]
    charge = elig["hospital_charge"]
    if status in ("eligible", "requires_pre_authorization"):
        insurer, patient = elig["insurer_pays"], elig["patient_pays"]
        summary = f"Estimated cost of {proc['name']}: {inr(charge)}. Insurance pays {inr(insurer)}, you pay {inr(patient)}."
        if status == "requires_pre_authorization":
            summary += f" This assumes pre-authorisation is approved; without it you would pay the full {inr(charge)}."
    else:
        insurer, patient = money(0), charge
        summary = (f"Estimated cost of {proc['name']}: {inr(charge)}, payable by you in full "
                   f"(insurance status: {status.replace('_', ' ')} – {elig['reasons'][-1]})")
    return ToolResult(tool="calculate_estimated_bill", status="success", answer_type="calculated", source=SOURCE,
                      summary=summary,
                      data={"procedure_code": proc["procedure_code"], "procedure": proc["name"],
                            "service_date": service_date.isoformat(), "hospital_charge": charge,
                            "insurer_pays": insurer, "patient_pays": patient, "insurance_status": status},
                      references=["hospital price list " + proc["procedure_code"], *elig["references"]],
                      notes=["Estimate covers the procedure charge only (not room, medicines or extra tests). "
                             "The final bill may differ."])
