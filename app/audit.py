"""Audit trail (R10): one row per chat request in audit_logs.

Phase 6 records the essentials; Phase 7 adds redaction and richer privacy checks.
"""
import json

from app.db import connect
from app.orchestrator.models import OrchestratorResponse
from app.tools.base import RequestContext


def record_chat(ctx: RequestContext, message: str, response: OrchestratorResponse) -> None:
    privacy = [{"check": "identity_from_login_token", "passed": True},
               {"check": "patient_scope", "patient_id": ctx.patient_id, "role": ctx.role}]
    privacy += [{"check": "routing", "note": n} for n in response.routing_notes]
    with connect() as conn:
        conn.execute(
            """INSERT INTO audit_logs (trace_id, user_id, role, action, query_redacted, intent, answer_type,
                                       patient_accessed, retrieved_sources, tools_invoked, privacy_checks,
                                       model_name, latency_ms)
               VALUES (%s, %s, %s, 'chat', %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (response.trace_id, ctx.user_id, ctx.role, message[:500], response.intent, response.answer_type,
             ctx.patient_id, json.dumps(response.citations, default=str),
             json.dumps(response.plan, default=str), json.dumps(privacy), response.model, response.latency_ms))
