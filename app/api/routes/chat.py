"""Login and chat endpoints."""
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator

from app import audit
from app.api.auth import (LOCK_MINUTES, CurrentUser, authenticate, clear_session_cookie, create_token,
                          current_user, set_session_cookie)
from app.api.ratelimit import enforce
from app.api.sessions import load_session, save_session
from app.db import connect
from app.orchestrator.models import OrchestratorResponse
from app.orchestrator.service import Orchestrator

router = APIRouter(prefix="/api")


@lru_cache
def get_orchestrator() -> Orchestrator:
    """One shared orchestrator (models load once). Tests replace it via dependency_overrides."""
    return Orchestrator()


# ---------------------------------------------------------------- auth
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response):
    client_ip = request.client.host if request.client else None
    enforce("login", client_ip or "unknown", "LOGIN_RATE_LIMIT", 10)
    user, outcome = authenticate(body.username, body.password)
    audit.record_security_event(trace_id=request.state.trace_id, action=f"login_{outcome}",
                                username=body.username, user_id=user.user_id if user else None,
                                role=user.role if user else None, outcome=outcome, client_ip=client_ip)
    if outcome == "locked":
        raise HTTPException(429, f"Too many failed attempts. Try again in {LOCK_MINUTES} minutes.")
    if user is None:
        raise HTTPException(401, "Invalid username or password")   # same message for every failure
    token = create_token(user)
    set_session_cookie(response, token)        # browsers use the httpOnly cookie
    return {"access_token": token, "token_type": "bearer", "user": me(user)}   # token for API clients


@router.post("/auth/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"status": "logged_out"}


@router.get("/me")
def me(user: CurrentUser = Depends(current_user)):
    info = {"user_id": user.user_id, "username": user.auth_subject, "display_name": user.display_name,
            "role": user.role, "patient_id": user.patient_id}
    if user.role == "staff":
        with connect(user_id=user.user_id) as conn:  # narrow view: only this staff member's patients
            info["assigned_patients"] = conn.execute(
                "SELECT patient_id, full_name FROM my_assigned_patients ORDER BY patient_id").fetchall()
    return info


# ---------------------------------------------------------------- chat
class ChatRequest(BaseModel):
    message: str = Field("", max_length=2000)
    session_id: str | None = None
    patient_id: str | None = Field(None, description="Staff only: which assigned patient this chat is about")
    decision: Literal["confirm", "decline"] | None = None
    confirmation_id: str | None = None

    @model_validator(mode="after")
    def message_or_decision(self):
        if not self.message.strip() and not self.decision:
            raise ValueError("message is required")
        if self.decision and not self.confirmation_id:
            raise ValueError("confirmation_id is required with a decision")
        return self


class ChatResponse(OrchestratorResponse):
    session_id: str


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, request: Request, user: CurrentUser = Depends(current_user),
         orchestrator: Orchestrator = Depends(get_orchestrator)):
    enforce("chat", user.user_id, "CHAT_RATE_LIMIT", 20)
    request.state.audited_by_route = True   # the chat writes its own, richer audit row
    session_id, session = load_session(user, body.session_id, body.patient_id)
    if body.decision:  # a button press must refer to THIS session's pending change
        pending = session.pending_confirmation or {}
        if pending.get("confirmation_id") != body.confirmation_id:
            raise HTTPException(409, "There is no matching change waiting for confirmation")
    message = body.message.strip() or ("yes" if body.decision == "confirm" else "no")
    response = orchestrator.handle(session, message, decision=body.decision, trace_id=request.state.trace_id)
    save_session(session_id, user, session)
    audit.record_chat(session.ctx, message, response)
    return ChatResponse(**response.model_dump(), session_id=session_id)


@router.get("/chat/{session_id}")
def chat_history(session_id: str, user: CurrentUser = Depends(current_user)):
    _, session = load_session(user, session_id)
    return {"session_id": session_id, "history": [{"role": r, "text": t} for r, t in session.history],
            "pending_confirmation": session.pending_confirmation}
