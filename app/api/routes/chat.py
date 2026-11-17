"""Login and chat endpoints."""
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app import audit
from app.api.auth import CurrentUser, authenticate, create_token, current_user
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
def login(body: LoginRequest):
    user = authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(401, "Invalid username or password")   # same message for every failure
    return {"access_token": create_token(user), "token_type": "bearer", "user": me(user)}


@router.get("/me")
def me(user: CurrentUser = Depends(current_user)):
    info = {"user_id": user.user_id, "username": user.auth_subject, "display_name": user.display_name,
            "role": user.role, "patient_id": user.patient_id}
    if user.role == "staff":
        with connect() as conn:
            info["assigned_patients"] = conn.execute(
                """SELECT p.patient_id, p.full_name FROM care_assignments c JOIN patients p USING (patient_id)
                   WHERE c.staff_user_id = %s ORDER BY p.patient_id""", (user.user_id,)).fetchall()
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
def chat(body: ChatRequest, user: CurrentUser = Depends(current_user),
         orchestrator: Orchestrator = Depends(get_orchestrator)):
    session_id, session = load_session(user, body.session_id, body.patient_id)
    if body.decision:  # a button press must refer to THIS session's pending change
        pending = session.pending_confirmation or {}
        if pending.get("confirmation_id") != body.confirmation_id:
            raise HTTPException(409, "There is no matching change waiting for confirmation")
    message = body.message.strip() or ("yes" if body.decision == "confirm" else "no")
    response = orchestrator.handle(session, message, decision=body.decision)
    save_session(session_id, user, session)
    audit.record_chat(session.ctx, message, response)
    return ChatResponse(**response.model_dump(), session_id=session_id)


@router.get("/chat/{session_id}")
def chat_history(session_id: str, user: CurrentUser = Depends(current_user)):
    _, session = load_session(user, session_id)
    return {"session_id": session_id, "history": [{"role": r, "text": t} for r, t in session.history],
            "pending_confirmation": session.pending_confirmation}
