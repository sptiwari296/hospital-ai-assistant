"""Tool 4 — Medication interaction checker (Pharmacy System).

Outcomes come ONLY from the approved formulary table `drug_interactions`.
Unknown medicine names are reported as unrecognised — never guessed.
"No listed interaction" is NOT reported as "safe".
"""
from itertools import combinations

from app.db import connect
from app.tools.base import RequestContext, ToolInputError, ToolResult, require_patient, tool

SOURCE = "Pharmacy System – approved interaction formulary"
SEVERITY_ORDER = {"contraindicated": 0, "major": 1, "moderate": 2, "minor": 3}
NOTE = ("Interaction results come from the hospital formulary. Do not start, stop or change any medicine "
        "without talking to your doctor or pharmacist.")


def _resolve(conn, names: list[str]) -> tuple[dict[str, str], list[str]]:
    """Map names to drug codes via code, generic or brand name. Returns (code->display name, unrecognised)."""
    found, unknown = {}, []
    for name in names:
        n = name.strip().lower()
        row = conn.execute("""SELECT drug_code, generic_name FROM medications
                              WHERE lower(drug_code) = %s OR lower(generic_name) = %s OR lower(brand_name) = %s""",
                           (n.replace(" ", "_"), n, n)).fetchone()
        if row:
            found[row["drug_code"]] = row["generic_name"]
        elif n:
            unknown.append(name.strip())
    return found, unknown


def _check(conn, names: list[str], tool_name: str) -> ToolResult:
    found, unknown = _resolve(conn, names)
    if len(found) < 2:
        raise ToolInputError("Please give at least two medicines I can recognise"
                             + (f" (not recognised: {', '.join(unknown)})" if unknown else "") + ".")
    pairs = [tuple(sorted(p)) for p in combinations(found, 2)]
    rows = conn.execute("""SELECT * FROM drug_interactions WHERE (drug_a, drug_b) IN
                           (SELECT * FROM unnest(%s::text[], %s::text[]))""",
                        ([a for a, _ in pairs], [b for _, b in pairs])).fetchall()
    interactions = sorted(({"medicines": [found[r["drug_a"]], found[r["drug_b"]]], "severity": r["severity"],
                            "description": r["description"], "source": r["source"]} for r in rows),
                          key=lambda i: SEVERITY_ORDER[i["severity"]])
    checked = ", ".join(found.values())
    if interactions:
        summary = f"Checked {checked}. Interactions found:\n" + "\n".join(
            f"- {' + '.join(i['medicines'])}: {i['severity'].upper()} – {i['description']}" for i in interactions)
    else:
        summary = (f"Checked {checked}. No interaction between these medicines is listed in the hospital formulary. "
                   "This does not guarantee the combination is safe for you.")
    if unknown:
        summary += f"\nNot recognised (not checked): {', '.join(unknown)}."
    return ToolResult(tool=tool_name, status="success", answer_type="calculated", source=SOURCE, summary=summary,
                      data={"checked": list(found.values()), "unrecognised": unknown, "interactions": interactions,
                            "highest_severity": interactions[0]["severity"] if interactions else None},
                      references=[f"formulary pair {'+'.join(p)}" for p in pairs], notes=[NOTE])


@tool("drug_interaction_check", SOURCE)
def drug_interaction_check(ctx: RequestContext, medication_list: list[str]) -> ToolResult:
    """Check a list of medicine names against each other (no patient data needed)."""
    with connect() as conn:
        return _check(conn, medication_list, "drug_interaction_check")


@tool("get_active_medications", SOURCE)
def get_active_medications(ctx: RequestContext) -> ToolResult:
    pid = require_patient(ctx)
    with connect(pid) as conn:
        rows = conn.execute("""SELECT p.prescription_id, m.generic_name, p.dose, p.frequency, p.start_date,
                                      d.full_name AS prescriber
                               FROM prescriptions p JOIN medications m USING (drug_code)
                               JOIN doctors d ON d.doctor_id = p.doctor_id
                               WHERE p.patient_id = %s AND p.status = 'active' ORDER BY m.generic_name""",
                            (pid,)).fetchall()
    if not rows:
        return ToolResult(tool="get_active_medications", status="not_found", answer_type="not_found", source=SOURCE,
                          summary="You have no active prescriptions on record.")
    return ToolResult(tool="get_active_medications", status="success", answer_type="calculated", source=SOURCE,
                      summary="Your active medicines: " + "; ".join(
                          f"{r['generic_name']} {r['dose']} {r['frequency']}" for r in rows) + ".",
                      data={"medications": [{**r, "start_date": r["start_date"].isoformat()} for r in rows]},
                      references=[r["prescription_id"] for r in rows])


@tool("check_new_medication", SOURCE)
def check_new_medication(ctx: RequestContext, medication_list: list[str]) -> ToolResult:
    """'Can I take ibuprofen?' -> check the new medicine(s) against the patient's active prescriptions."""
    pid = require_patient(ctx)
    with connect(pid) as conn:
        active = [r["generic_name"] for r in conn.execute(
            """SELECT m.generic_name FROM prescriptions p JOIN medications m USING (drug_code)
               WHERE p.patient_id = %s AND p.status = 'active'""", (pid,)).fetchall()]
        result = _check(conn, active + medication_list, "check_new_medication")
    result.data["your_active_medicines"] = active
    return result
