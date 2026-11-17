"""Tool 2 — Insurance eligibility (Insurance Validation System).

Deterministic rule engine. The LLM never decides coverage (R5). Checks, in order:
  1 active policy on the service date        2 hospital in the insurer's network
  3 coverage rule in force ON THE SERVICE DATE (so 2027 treatment uses the 2027 agreement)
  4 exclusions (not_covered)                 5 waiting period since joining the plan
  6 rule conditions (e.g. documented diagnosis)
  7 amounts: coverage % capped at the limit  8 pre-authorisation status
Statuses: eligible | requires_pre_authorization | not_covered | not_eligible | no_active_policy | manual_review
"""
from datetime import date, timedelta
from decimal import Decimal

from app import clock
from app.db import connect
from app.tools.base import RequestContext, ToolResult, fmt_date, inr, mask, money, require_patient, tool
from app.tools.catalog import resolve_procedure

SOURCE = "Insurance Validation System"
NOTE = ("This is an estimate from the hospital's insurance rules. The final decision is made by the insurer "
        "when the claim is processed.")


def evaluate_eligibility(conn, pid: str, procedure: dict, service_date: date) -> dict:
    """Pure rule evaluation, reused by the billing estimate. Returns a dict with status, reasons and amounts."""
    out = {"procedure_code": procedure["procedure_code"], "procedure": procedure["name"],
           "service_date": service_date.isoformat(), "reasons": [], "references": []}
    cost = money(procedure["base_cost"])
    out |= {"hospital_charge": cost, "insurer_pays": money(0), "patient_pays": cost}

    def finish(status: str, reason: str) -> dict:
        out["eligibility_status"] = status
        out["reasons"].append(reason)
        return out

    # 1. policy active on the service date
    policies = conn.execute(
        """SELECT p.*, pl.provider, pl.plan_name, pl.hospital_in_network
           FROM insurance_policies p JOIN insurance_plans pl USING (plan_id)
           WHERE p.patient_id = %s ORDER BY p.valid_to DESC""", (pid,)).fetchall()
    if not policies:
        return finish("no_active_policy", "No insurance policy is registered for you.")
    policy = next((p for p in policies if p["valid_from"] <= service_date <= p["valid_to"]
                   and p["status"] != "suspended"), None)
    latest = policies[0]
    out |= {"plan": f"{latest['provider']} {latest['plan_name']}", "policy_number": mask(latest["policy_number"])}
    if policy is None:
        return finish("not_eligible", f"Your policy is not active on {fmt_date(service_date)} "
                                      f"(valid {fmt_date(latest['valid_from'])} to {fmt_date(latest['valid_to'])}).")
    out["policy_valid_to"] = policy["valid_to"].isoformat()

    # 2. network
    if not policy["hospital_in_network"]:
        return finish("not_eligible", f"This hospital is not in the {policy['provider']} network for your plan.")

    # 3. rule in force on the service date
    rule = conn.execute(
        """SELECT * FROM coverage_rules WHERE plan_id = %s AND procedure_code = %s
             AND effective_from <= %s AND (effective_to IS NULL OR effective_to >= %s)
           ORDER BY effective_from DESC LIMIT 1""",
        (policy["plan_id"], procedure["procedure_code"], service_date, service_date)).fetchone()
    if rule is None:
        return finish("manual_review", "Your plan has no rule for this procedure. The Insurance Desk must check "
                                       "with the insurer.")
    out["references"] += [f"coverage rule {rule['rule_id']}", rule["source_document"]]
    out |= {"coverage_percent": rule["coverage_percent"],
            "coverage_limit": money(rule["max_amount"]) if rule["max_amount"] is not None else None}

    # 4. exclusions
    if rule["coverage_status"] == "not_covered":
        why = rule["conditions"].get("exclusion", "excluded by the plan")
        return finish("not_covered", f"{procedure['name']} is not covered by your plan ({why}).")

    # 5. waiting period
    if rule["waiting_period_days"]:
        eligible_from = policy["member_since"] + timedelta(days=rule["waiting_period_days"])
        out["waiting_period_ends"] = eligible_from.isoformat()
        if service_date < eligible_from:
            return finish("not_eligible", f"The {rule['waiting_period_days'] // 365}-year waiting period for this "
                                          f"procedure ends on {fmt_date(eligible_from)} (member since "
                                          f"{fmt_date(policy['member_since'])}).")

    # 6. conditions
    required_dx = rule["conditions"].get("required_diagnosis")
    if required_dx:
        conditions = conn.execute("SELECT chronic_conditions FROM patients WHERE patient_id = %s",
                                  (pid,)).fetchone()["chronic_conditions"]
        if required_dx not in conditions:
            return finish("manual_review", f"Coverage requires a documented diagnosis ({required_dx.replace('_', ' ')}) "
                                           "which is not in your record. Please speak to the Insurance Desk.")

    # 7. amounts (deterministic, capped)
    covered = money(cost * Decimal(rule["coverage_percent"]) / 100)
    if rule["max_amount"] is not None:
        covered = min(covered, money(rule["max_amount"]))
    out |= {"insurer_pays": covered, "patient_pays": money(cost - covered)}
    amounts = (f"Estimated: insurer pays {inr(covered)} ({rule['coverage_percent']}%"
               + (f", limit {inr(rule['max_amount'])}" if rule["max_amount"] is not None else "")
               + f"), you pay {inr(cost - covered)} of the {inr(cost)} hospital charge.")

    # 8. pre-authorisation
    if rule["requires_preauth"]:
        auth = conn.execute(
            """SELECT * FROM prior_authorizations WHERE patient_id = %s AND procedure_code = %s AND policy_id = %s
               ORDER BY requested_at DESC LIMIT 1""", (pid, procedure["procedure_code"], policy["policy_id"])).fetchone()
        if auth and auth["status"] == "approved" and (auth["valid_until"] is None or auth["valid_until"] >= service_date):
            out["pre_authorization"] = {"id": auth["auth_id"], "status": "approved"}
            out["reasons"].append("Pre-authorisation is approved.")
            return finish("eligible", amounts)
        if auth and auth["status"] == "denied":
            out["pre_authorization"] = {"id": auth["auth_id"], "status": "denied"}
            return finish("not_eligible", "The insurer denied pre-authorisation for this procedure.")
        if auth:
            out["pre_authorization"] = {"id": auth["auth_id"], "status": auth["status"],
                                        "requested_on": auth["requested_at"].date().isoformat()}
            out["reasons"].append(f"Pre-authorisation is required and is {auth['status']} "
                                  f"(requested {fmt_date(auth['requested_at'])}).")
        else:
            out["pre_authorization"] = {"status": "not_requested"}
            out["reasons"].append("Pre-authorisation is required and has not been requested yet.")
        return finish("requires_pre_authorization", amounts + " These amounts apply once pre-authorisation is approved.")
    return finish("eligible", amounts)


@tool("check_insurance_eligibility", SOURCE)
def check_insurance_eligibility(ctx: RequestContext, procedure: str, service_date: date | None = None) -> ToolResult:
    pid = require_patient(ctx)
    service_date = service_date or clock.today()
    with connect() as conn:
        result = evaluate_eligibility(conn, pid, resolve_procedure(conn, procedure), service_date)
    labels = {"eligible": "Eligible", "requires_pre_authorization": "Covered, but pre-authorisation is required",
              "not_covered": "Not covered", "not_eligible": "Not eligible", "no_active_policy": "No active policy",
              "manual_review": "Needs manual review by the Insurance Desk"}
    return ToolResult(tool="check_insurance_eligibility", status="success", answer_type="calculated", source=SOURCE,
                      summary=f"{labels[result['eligibility_status']]} – {result['procedure']} on "
                              f"{fmt_date(service_date)}. " + " ".join(result["reasons"]),
                      data={k: v for k, v in result.items() if k != "references"},
                      references=result["references"], notes=[NOTE])
