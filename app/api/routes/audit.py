"""Audit endpoints (R10). System admins see every trace; other users only their own."""
from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import CurrentUser, current_user, require_role
from app.db import connect

router = APIRouter(prefix="/api/audit")


@router.get("/{trace_id}")
def get_trace(trace_id: str, user: CurrentUser = Depends(current_user)):
    with connect() as conn:
        row = conn.execute("SELECT * FROM audit_logs WHERE trace_id = %s", (trace_id,)).fetchone()
    if not row or (user.role != "sys_admin" and row["user_id"] != user.user_id):
        raise HTTPException(404, "Trace not found")   # other users' traces look like missing ones
    return row


@router.get("")
def recent(limit: int = 50, user: CurrentUser = Depends(require_role("sys_admin"))):
    with connect() as conn:
        return conn.execute("""SELECT trace_id, user_id, role, action, intent, answer_type, latency_ms, created_at
                               FROM audit_logs ORDER BY created_at DESC LIMIT %s""", (min(limit, 200),)).fetchall()
