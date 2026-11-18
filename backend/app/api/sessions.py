"""Persist chat Session state in Postgres between HTTP requests.

The RequestContext (identity) is rebuilt from the login token on EVERY request and is
never read from storage — stored state only holds conversation data.
"""
import json
import uuid

from fastapi import HTTPException

from app.api.auth import CurrentUser
from app.db import connect
from app.orchestrator.models import Session
from app.tools.base import AuthorizationError, build_context


def new_session_id() -> str:
    return f"SES-{uuid.uuid4().hex[:12].upper()}"


def load_session(user: CurrentUser, session_id: str | None, acting_for_patient: str | None = None) -> tuple[str, Session]:
    state, acting_for = {}, acting_for_patient
    if session_id:
        with connect() as conn:
            row = conn.execute("SELECT * FROM chat_sessions WHERE session_id = %s", (session_id,)).fetchone()
        if not row or row["user_id"] != user.user_id:      # someone else's session looks like a missing one
            raise HTTPException(404, "Chat session not found")
        state = row["state"]
        acting_for = acting_for_patient or row["acting_for_patient"]
    else:
        session_id = new_session_id()
    try:
        ctx = build_context(user.auth_subject, acting_for_patient=acting_for if user.role == "staff" else None)
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from None
    session = Session(ctx=ctx, history=[tuple(h) for h in state.get("history", [])],
                      offered_slots=state.get("offered_slots"), pending_confirmation=state.get("pending_confirmation"))
    return session_id, session


def save_session(session_id: str, user: CurrentUser, session: Session) -> None:
    state = {"history": session.history, "offered_slots": session.offered_slots,
             "pending_confirmation": session.pending_confirmation}
    acting_for = session.ctx.patient_id if user.role == "staff" else None
    with connect() as conn:
        conn.execute(
            """INSERT INTO chat_sessions (session_id, user_id, acting_for_patient, state)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (session_id) DO UPDATE SET state = EXCLUDED.state,
                   acting_for_patient = EXCLUDED.acting_for_patient, updated_at = now()""",
            (session_id, user.user_id, acting_for, json.dumps(state, default=str)))
