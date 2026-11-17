"""Tool 3 — Laboratory results (LIS) and radiology reports (RIS).

Reports values exactly as the laboratory recorded them, with unit, reference range,
the LAB's own flag, dates and the record id. It never interprets a value as a diagnosis.
"""
from app.db import connect
from app.tools.base import RequestContext, ToolInputError, ToolResult, fmt_date, require_patient, tool

LIS = "Laboratory Information System"
RIS = "Radiology Information System"
NO_DIAGNOSIS_NOTE = ("Values outside the reference range are flagged by the laboratory. This is not a diagnosis; "
                     "please discuss what the result means for you with your doctor.")

# what patients say -> laboratory test code (longest alias wins)
TEST_ALIASES = {
    "HBA1C": ["hba1c", "hb a1c", "a1c", "glycated", "glycosylated", "average sugar"],
    "FBS": ["fasting blood sugar", "fasting sugar", "fasting glucose", "blood sugar", "blood glucose", "glucose",
            "sugar", "fbs"],
    "HGB": ["haemoglobin", "hemoglobin", "hgb", "hb"],
    "WBC": ["white blood cell", "white cell", "wbc"],
    "PLT": ["platelet"],
    "CREAT": ["creatinine", "kidney function"],
    "TSH": ["tsh", "thyroid"],
    "INR": ["inr", "prothrombin", "clotting"],
    "LDL": ["ldl", "cholesterol"],
}


def resolve_test(text: str) -> str | None:
    t = text.strip().lower()
    if t.upper() in TEST_ALIASES:
        return t.upper()
    matches = [(len(alias), code) for code, aliases in TEST_ALIASES.items() for alias in aliases if alias in t]
    return max(matches)[1] if matches else None


def _row(r: dict) -> dict:
    ref = f"{float(r['ref_low']):g}–{float(r['ref_high']):g} {r['unit']}" if r["ref_low"] is not None else None
    return {"result_id": r["result_id"], "test": r["test_name"], "value": float(r["value"]), "unit": r["unit"],
            "reference_range": ref, "flag": r["flag"], "status": r["status"],
            "collected_at": r["collected_at"].isoformat(timespec="minutes"),
            "reported_at": r["reported_at"].isoformat(timespec="minutes"),
            "order_id": r["order_id"], "report_reference": f"LIS result {r['result_id']}"}


@tool("get_lab_results", LIS)
def get_lab_results(ctx: RequestContext, test_name: str, limit: int = 5) -> ToolResult:
    pid = require_patient(ctx)
    code = resolve_test(test_name)
    with connect() as conn:
        rows = conn.execute("""SELECT * FROM lab_results WHERE patient_id = %s AND test_code = %s
                               ORDER BY collected_at DESC LIMIT %s""", (pid, code, max(1, min(limit, 20)))).fetchall() \
            if code else []
        if not rows:
            available = [r["test_name"] for r in conn.execute(
                "SELECT DISTINCT test_name FROM lab_results WHERE patient_id = %s ORDER BY 1", (pid,)).fetchall()]
    if not code:
        raise ToolInputError(f"I could not identify the test '{test_name}'. Tests in your record: "
                             f"{', '.join(available) or 'none'}.")
    if not rows:
        return ToolResult(tool="get_lab_results", status="not_found", answer_type="not_found", source=LIS,
                          summary=f"No {test_name} results were found in your record. Tests on file: "
                                  f"{', '.join(available) or 'none'}.")
    results = [_row(r) for r in rows]
    latest = results[0]
    flag = "" if latest["flag"] == "normal" else f" – flagged {latest['flag'].upper()} by the lab"
    summary = (f"Your latest {latest['test']} is {latest['value']:g} {latest['unit']} (collected "
               f"{fmt_date(rows[0]['collected_at'])}; reference range {latest['reference_range']}){flag}.")
    if len(results) > 1:
        summary += " Earlier: " + ", ".join(f"{r['value']:g} {r['unit']} ({fmt_date(row['collected_at'])})"
                                            for r, row in zip(results[1:], rows[1:]))
    return ToolResult(tool="get_lab_results", status="success", answer_type="calculated", source=LIS,
                      summary=summary, data={"test_code": code, "latest": latest, "results": results},
                      references=[r["report_reference"] for r in results], notes=[NO_DIAGNOSIS_NOTE])


def get_latest_lab_result(ctx: RequestContext, test_name: str) -> ToolResult:
    return get_lab_results(ctx, test_name, limit=1)


@tool("get_radiology_reports", RIS)
def get_radiology_reports(ctx: RequestContext, limit: int = 5) -> ToolResult:
    pid = require_patient(ctx)
    with connect() as conn:
        rows = conn.execute("""SELECT r.*, d.full_name AS radiologist FROM radiology_reports r
                               LEFT JOIN doctors d ON d.doctor_id = r.radiologist_id
                               WHERE r.patient_id = %s ORDER BY performed_at DESC LIMIT %s""",
                            (pid, max(1, min(limit, 20)))).fetchall()
    if not rows:
        return ToolResult(tool="get_radiology_reports", status="not_found", answer_type="not_found", source=RIS,
                          summary="No radiology reports were found in your record.")
    reports = [{"report_id": r["report_id"], "modality": r["modality"], "body_part": r["body_part"],
                "performed_at": r["performed_at"].isoformat(timespec="minutes"), "findings": r["findings"],
                "impression": r["impression"], "radiologist": r["radiologist"], "status": r["status"]} for r in rows]
    return ToolResult(tool="get_radiology_reports", status="success", answer_type="calculated", source=RIS,
                      summary="Your radiology reports:\n" + "\n".join(
                          f"{fmt_date(r['performed_at'])} {r['modality']} {r['body_part']}: \"{r['impression']}\" "
                          f"(report {r['report_id']})" for r in rows),
                      data={"reports": reports}, references=[f"RIS report {r['report_id']}" for r in rows],
                      notes=["Report text is quoted as written by the radiologist. Your doctor will explain it."])
