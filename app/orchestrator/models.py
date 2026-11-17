"""Data shapes for the orchestrator."""
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from app.tools.base import RequestContext

AnswerType = Literal["retrieved_fact", "calculated", "action_completed", "clarification_needed",
                     "not_found", "refused", "conflict_flagged"]

INTENTS = {
    "knowledge_query": "Question about hospital policies, procedures, rules, visiting, preparation, insurance plan terms "
                       "in general (answered from approved documents).",
    "appointment_query": "The user's own appointments (when, where, with whom).",
    "appointment_change": "Reschedule or cancel one of the user's appointments, or choose one of the offered slots.",
    "insurance_query": "Whether the user's own insurance covers a procedure, pre-authorisation, coverage amount.",
    "lab_query": "The user's own lab results or radiology reports.",
    "medication_query": "The user's medicines, or whether medicines interact / can be taken together.",
    "billing_query": "The user's bills, payments, balance, or the estimated cost of a procedure.",
    "care_query": "The user's pending orders, pre-operative test status, surgery schedule or readiness checklist "
                  "(one specific item).",
    "surgery_readiness": "Whether the user can proceed with / is ready for their planned surgery (overall status).",
    "mixed": "Needs both hospital documents and the user's own records in one answer.",
    "greeting": "Greeting, thanks, or asking what the assistant can do.",
    "medical_advice": "Asks for a diagnosis, treatment decision, dosing change or interpretation of their health "
                      "beyond what records state.",
    "other_patient": "Asks about another person's records or data.",
    "emergency": "Describes an urgent medical emergency happening now.",
    "out_of_scope": "Anything unrelated to this hospital's patient services.",
}
Intent = Literal[tuple(INTENTS)]  # type: ignore[valid-type]


class ProposedToolCall(BaseModel):
    tool: str
    arguments_json: str  # JSON object as text; validated against the tool's input model by the backend


class IntentClassification(BaseModel):
    """What the LLM classifier proposes. NOT an authorization decision — the backend validates it."""
    intent: Intent
    requires_authentication: bool
    requires_rag: bool
    rag_query: str
    requires_tools: bool
    tool_calls: list[ProposedToolCall]
    requires_confirmation: bool
    target_hint: str            # which appointment / item the user means, e.g. "cardiology"
    procedure: str              # procedure mentioned, e.g. "knee replacement", or ""
    clarification_question: str

    @property
    def tools(self) -> list[str]:
        return [t.tool for t in self.tool_calls]


@dataclass
class StepRecord:
    """One executed step of the plan — auditable."""
    step: int
    name: str                   # tool name or "rag"
    arguments: dict[str, Any]
    status: str
    answer_type: str
    latency_ms: int
    summary: str = ""


@dataclass
class Session:
    """Conversation state between turns (Phase 6 stores it per login session)."""
    ctx: RequestContext
    history: list[tuple[str, str]] = field(default_factory=list)  # (role, text), short
    offered_slots: dict[str, Any] | None = None                     # {"appointment_id", "slots": [...]}
    pending_confirmation: dict[str, Any] | None = None              # {"confirmation_id", "summary"}

    def remember(self, user: str, assistant: str) -> None:
        self.history = (self.history + [("user", user), ("assistant", assistant)])[-8:]


class OrchestratorResponse(BaseModel):
    """The structured final response (matches the brief's example response)."""
    trace_id: str
    answer: str
    answer_type: AnswerType
    citations: list[dict[str, Any]] = []
    tools_invoked: list[dict[str, Any]] = []
    explanation: str = ""
    intent: str = ""
    requires_confirmation: bool = False
    confirmation_id: str | None = None
    escalation: dict[str, Any] | None = None
    conflicts: list[dict[str, Any]] = []
    routing_notes: list[str] = []       # what the backend changed or rejected in the proposed routing
    plan: list[dict[str, Any]] = []
    model: str | None = None
    latency_ms: int = 0
