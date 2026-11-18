"""Redaction of protected information before anything is written to logs or the audit trail.

Logs must be useful for investigations without becoming a second copy of patient data.
Patient ids (P1001) are kept on purpose: an attempt to access another patient must stay visible.
"""
import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
NATIONAL_ID = re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}\b")
POLICY = re.compile(r"\b[A-Z]{2,5}-\d{4}-\d{4,8}\b")
PHONE_CANDIDATE = re.compile(r"\+?\d[\d\s()-]{7,}\d")


def _phone(m: re.Match) -> str:
    digits = re.sub(r"\D", "", m.group())
    return "[PHONE]" if len(digits) >= 10 else m.group()   # dates like 2026-11-26 have only 8 digits


def redact(text: str | None, max_len: int = 300) -> str | None:
    if text is None:
        return None
    text = EMAIL.sub("[EMAIL]", text)
    text = POLICY.sub("[POLICY_NO]", text)
    text = NATIONAL_ID.sub("[ID_NO]", text)
    text = PHONE_CANDIDATE.sub(_phone, text)
    return text[:max_len]
