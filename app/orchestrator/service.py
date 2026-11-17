"""The AI orchestrator — one entry point for every user message.

  message ─► pending confirmation? (user's yes/no executes or declines; no LLM)
          ─► deterministic safety checks (emergency, other patient's id)
          ─► LLM intent classification (proposal)
          ─► routing policy (backend validation & permissions)
          ─► execute: RAG | tools | appointment workflow | surgery-readiness plan
          ─► compose structured, cited response (answer_type from section 6 of the brief)
"""
import re
import time
import uuid
from dataclasses import replace

from app.orchestrator.classifier import (IntentClassifier, LLMIntentClassifier, detect_emergency,
                                         mentioned_other_patient)
from app.orchestrator.composer import compose
from app.orchestrator.models import OrchestratorResponse, Session, StepRecord
from app.orchestrator.routing import validate_route
from app.orchestrator.workflows import (Executor, Part, appointment_change, part_from, pick_offered_option,
                                        surgery_readiness)
from app.rag.generator import LLMError
from app.rag.models import NOT_FOUND_MESSAGE
from app.rag.service import RagService
from app.tools import appointments

YES = re.compile(r"^\s*(yes|y|yeah|yep|confirm|confirmed|go ahead|ok|okay|sure|please do)\b[\s.!]*$", re.IGNORECASE)
NO = re.compile(r"^\s*(no|n|nope|cancel that|don'?t|do not|stop|never ?mind)\b[\s.!]*$", re.IGNORECASE)

FIXED = {
    "emergency": Part("refused", "This sounds like an emergency. Please go to the Emergency Department (Ground Floor, "
                      "Block C, open 24 hours) or call emergency services now. This assistant cannot handle "
                      "emergencies.", ["Emergency procedures"], ["DOC010 §4", "DOC010 §1.2"],
                      escalation={"required": True, "to": ["emergency services"], "reason": "possible emergency"}),
    "other_patient": Part("refused", "I can only share information from your own records."),
    "medical_advice": Part("refused", "I can't give a diagnosis or medical advice. Please discuss this with your "
                           "doctor. I can show you your records or explain hospital policies."),
    "greeting": Part("clarification_needed", "Hello! I can help with hospital policies and procedures, your "
                     "appointments (including rescheduling), lab results, insurance coverage, medicines, bills and "
                     "surgery preparation. What would you like to know?"),
    "out_of_scope": Part("not_found", NOT_FOUND_MESSAGE),
}


class Orchestrator:
    def __init__(self, classifier: IntentClassifier | None = None, rag: RagService | None = None):
        self.classifier = classifier or LLMIntentClassifier()
        self.rag = rag or RagService()

    def handle(self, session: Session, message: str, decision: str | None = None,
               trace_id: str | None = None) -> OrchestratorResponse:
        started = time.perf_counter()
        trace_id = trace_id or f"HSP-{uuid.uuid4().hex[:8].upper()}"   # the HTTP layer passes its request trace
        session.ctx = replace(session.ctx, trace_id=trace_id)
        ex = Executor(session)  # all per-request state lives here (the orchestrator is shared between requests)
        notes: list[str] = []
        intent, model, parts = "", None, []

        # 1. A pending change is decided ONLY by the user's own yes/no (UI button or plain yes/no text).
        if session.pending_confirmation:
            decision = decision or ("confirm" if YES.match(message) else "decline" if NO.match(message) else None)
            if decision:
                intent = "confirmation_decision"
                parts = [self._decide(ex, session, decision)]
        if not parts and detect_emergency(message):                                   # 2. safety pre-checks
            intent, parts = "emergency", [FIXED["emergency"]]
        if not parts and (other := mentioned_other_patient(message, session.ctx.patient_id)):
            intent, parts = "other_patient", [FIXED["other_patient"]]
            notes.append(f"blocked reference to another patient id {other}")
        if not parts and (slot_id := pick_offered_option(message, session)):          # option number reply
            intent = "appointment_change"
            parts = [appointment_change(ex, message, [("reschedule_appointment", {
                "appointment_id": session.offered_slots["appointment_id"], "slot_id": slot_id})], "")]

        if not parts:                                                                  # 3. classify
            try:
                cls, model = self.classifier.classify(message, session)
            except LLMError as exc:
                return self._finish(session, message, trace_id, started, ex, "unavailable", model, notes,
                                    [Part("not_found", "Sorry, the assistant is temporarily unavailable. Please try "
                                          "again shortly.")], explanation=f"classifier unavailable: {exc}")
            intent = cls.intent
            plan = validate_route(cls, session.ctx, message)                           # 4. backend validation
            notes += plan.notes
            parts = self._execute(ex, session, message, cls, plan)                     # 5. execute

        return self._finish(session, message, trace_id, started, ex, intent, model, notes, parts)

    # ---------------------------------------------------------------- execution
    def _execute(self, ex, session, message, cls, plan) -> list[Part]:
        if cls.intent in FIXED:
            return [FIXED[cls.intent]]
        if plan.refusal:
            return [Part("refused", plan.refusal)]
        if plan.clarification:
            return [Part("clarification_needed", plan.clarification)]
        if cls.intent == "appointment_change":
            return [appointment_change(ex, message, plan.tool_calls, cls.target_hint)]
        if cls.intent == "surgery_readiness":
            return [surgery_readiness(ex, cls.procedure)]

        parts = [part_from(ex.run(tool, args)) for tool, args in plan.tool_calls]
        if plan.run_rag:
            parts.append(self._rag(ex, session, plan.rag_query))
        return parts

    def _rag(self, ex: Executor, session: Session, query: str) -> Part:
        started = time.perf_counter()
        try:
            answer = self.rag.answer(query, trace_id=session.ctx.trace_id)
        except LLMError:
            return Part("not_found", "I could not look up the hospital documents right now.")
        ex.rag_model = answer.model
        ex.steps.append(StepRecord(step=len(ex.steps) + 1, name="rag", arguments={"query": query},
                                   status=answer.answer_type, answer_type=answer.answer_type,
                                   latency_ms=int((time.perf_counter() - started) * 1000), summary=answer.answer[:200]))
        return Part(answer.answer_type, answer.answer,
                    citations=[c.model_dump(mode="json") for c in answer.citations],
                    conflicts=[c.model_dump() for c in answer.conflicts])

    def _decide(self, ex: Executor, session: Session, decision: str) -> Part:
        pending = session.pending_confirmation
        session.pending_confirmation = None
        fn = appointments.confirm_action if decision == "confirm" else appointments.decline_action
        started = time.perf_counter()
        result = fn(session.ctx, pending["confirmation_id"])
        ex.steps.append(StepRecord(step=1, name=fn.tool_name, arguments={"confirmation_id": pending["confirmation_id"]},
                                   status=result.status, answer_type=result.answer_type,
                                   latency_ms=int((time.perf_counter() - started) * 1000), summary=result.summary))
        return part_from(result)

    # ---------------------------------------------------------------- response
    def _finish(self, session, message, trace_id, started, ex, intent, model, notes, parts,
                explanation: str = "") -> OrchestratorResponse:
        c = compose(parts)
        explanations = {
            "retrieved_fact": "Answered from approved hospital documents that are currently in force.",
            "calculated": "Produced by the hospital's systems using deterministic rules.",
            "action_completed": "The change was executed and verified in the source system.",
            "clarification_needed": "More information or a confirmation is needed.",
            "not_found": "The authorised sources do not contain this information.",
            "refused": "The request is not permitted or not safe for this assistant.",
            "conflict_flagged": "Approved documents conflict and the hierarchy cannot resolve it.",
        }
        response = OrchestratorResponse(
            trace_id=trace_id, answer=c["answer"], answer_type=c["answer_type"], citations=c["citations"],
            tools_invoked=[{"tool": s.name, "status": s.status, "latency_ms": s.latency_ms} for s in ex.steps],
            explanation=explanation or explanations[c["answer_type"]], intent=intent,
            requires_confirmation=bool(c["confirmation"]),
            confirmation_id=c["confirmation"]["confirmation_id"] if c["confirmation"] else None,
            escalation=c["escalation"], conflicts=c["conflicts"], routing_notes=notes,
            plan=[vars(s) for s in ex.steps], model=model or ex.rag_model,
            latency_ms=int((time.perf_counter() - started) * 1000))
        session.remember(message, response.answer)
        return response
