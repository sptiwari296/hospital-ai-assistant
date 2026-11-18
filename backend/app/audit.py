"""Audit trail (R10): one row per request.

Follows the brief's audit schema: trace_id, user_role, request_type, authorization_status,
retrieved_sources, tools_invoked, answer_type, model_version, latency_ms, timestamp.
Data minimisation: the question text is REDACTED, sources are stored as names/ids only, and
tool records hold the tool name, status and latency — never the patient data they returned.
The API's database role can only INSERT into audit_logs (append-only).
"""
import json

from app.db import connect
from app.orchestrator.models import OrchestratorResponse
from app.privacy import redact
from app.tools.base import RequestContext


def _source_names(citations: list[dict]) -> list[str]:
    out = []
    for c in citations:
        name = c.get("system") or (f"{c['document_id']} §{c['section']}" if c.get("section") else c.get("document_id"))
        if name and name not in out:
            out.append(name)
    return out


def authorization_status(response: OrchestratorResponse) -> str:
    return "denied" if response.answer_type == "refused" else "approved"


def record_chat(ctx: RequestContext, message: str, response: OrchestratorResponse) -> None:
    privacy = [{"check": "identity_from_login_token", "passed": True},
               {"check": "patient_scope", "patient_id": ctx.patient_id, "role": ctx.role},
               {"check": "db_row_level_security", "scope": ctx.patient_id}]
    privacy += [{"check": "routing", "note": redact(n)} for n in response.routing_notes]
    tools = [{"tool": t["tool"], "status": t["status"], "latency_ms": t["latency_ms"]} for t in response.tools_invoked]
    _insert(trace_id=response.trace_id, user_id=ctx.user_id, role=ctx.role, action="chat",
            query=redact(message), intent=response.intent, answer_type=response.answer_type,
            patient=ctx.patient_id, sources=_source_names(response.citations), tools=tools, privacy=privacy,
            model=response.model, latency=response.latency_ms, auth_status=authorization_status(response),
            endpoint="POST /api/chat", http_status=200)


def record_request(*, trace_id: str, user_id: str | None, role: str | None, endpoint: str, http_status: int,
                   patient: str | None, latency_ms: int, requested_patient: str | None = None) -> None:
    """Every authenticated non-chat API call (dashboard reads, actions, uploads, audit reads)."""
    status = "approved" if http_status < 400 else "denied" if http_status in (401, 403, 404, 409) else "error"
    privacy = [{"check": "patient_scope", "patient_id": patient, "requested": requested_patient}]
    _insert(trace_id=trace_id, user_id=user_id, role=role, action="api_request", query=None, intent=endpoint,
            answer_type=None, patient=patient, sources=[], tools=[], privacy=privacy, model=None,
            latency=latency_ms, auth_status=status, endpoint=endpoint, http_status=http_status)


def record_security_event(*, trace_id: str, action: str, username: str, user_id: str | None,
                          role: str | None, outcome: str, client_ip: str | None) -> None:
    """Login success / failure / lockout, rate-limit hits."""
    _insert(trace_id=trace_id, user_id=user_id, role=role, action=action, query=redact(username, 100),
            intent=None, answer_type=None, patient=None, sources=[], tools=[],
            privacy=[{"check": action, "outcome": outcome, "client_ip": client_ip}], model=None, latency=0,
            auth_status="approved" if outcome == "success" else "denied", endpoint=None, http_status=None)


def _insert(*, trace_id, user_id, role, action, query, intent, answer_type, patient, sources, tools, privacy,
            model, latency, auth_status, endpoint, http_status) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO audit_logs (trace_id, user_id, role, action, query_redacted, intent, answer_type,
                                       patient_accessed, retrieved_sources, tools_invoked, privacy_checks,
                                       model_name, latency_ms, authorization_status, endpoint, http_status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (trace_id) DO NOTHING""",
            (trace_id, user_id, role, action, query, intent, answer_type, patient, json.dumps(sources),
             json.dumps(tools), json.dumps(privacy, default=str), model, latency, auth_status, endpoint,
             http_status))
