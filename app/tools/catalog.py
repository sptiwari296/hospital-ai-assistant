"""Resolve what the user said ("knee replacement", "MRI of the brain") to a procedure code."""
from app.tools.base import ToolInputError

PROCEDURE_ALIASES = {
    "TKR": ["knee replacement", "total knee replacement", "tkr", "knee surgery", "knee arthroplasty"],
    "MRI_BRAIN": ["mri brain", "brain mri", "mri of the brain", "head mri"],
    "MRI_KNEE": ["mri knee", "knee mri", "mri of the knee"],
    "XR_KNEE": ["knee x-ray", "x-ray knee", "knee xray"],
    "CXR": ["chest x-ray", "chest xray", "cxr"],
    "ECG": ["ecg", "ekg", "electrocardiogram"],
    "CBC": ["cbc", "complete blood count", "blood count"],
    "HBA1C": ["hba1c", "a1c"],
    "PT_INR": ["pt/inr", "pt inr", "coagulation"],
    "ORTHO_CONSULT": ["orthopaedic consultation", "orthopedic consultation", "ortho consultation"],
    "CARDIO_CONSULT": ["cardiology consultation", "cardiac consultation", "heart consultation"],
    "GEN_CONSULT": ["general consultation", "general medicine consultation", "gp consultation"],
    "RHINOPLASTY": ["rhinoplasty", "nose job", "cosmetic nose surgery"],
}


def resolve_procedure(conn, text: str) -> dict:
    """Exact code -> alias -> catalogue name. Returns the procedures row or raises ToolInputError."""
    t = text.strip().lower()
    code = next((c for c in PROCEDURE_ALIASES if c.lower() == t), None)
    if code is None:
        matches = [(len(a), c) for c, aliases in PROCEDURE_ALIASES.items() for a in aliases if a in t]
        code = max(matches)[1] if matches else None
    if code is None:
        row = conn.execute("SELECT procedure_code FROM procedures WHERE lower(name) = %s", (t,)).fetchone()
        code = row["procedure_code"] if row else None
    row = conn.execute("SELECT * FROM procedures WHERE procedure_code = %s", (code,)).fetchone() if code else None
    if not row:
        names = [r["name"] for r in conn.execute("SELECT name FROM procedures ORDER BY name").fetchall()]
        raise ToolInputError(f"I could not identify the procedure '{text}'. Known procedures: {', '.join(names)}.")
    return row
