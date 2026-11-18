"""Phase 5 tests: the orchestrator with a SCRIPTED classifier (what the LLM proposes is fixed),
so we test how the backend validates, routes, executes and composes — deterministically."""
import json
from datetime import datetime

import pytest

from app import clock
from app.db import admin_connect as connect  # fixtures need the owner role (bypasses RLS)
from app.orchestrator.classifier import IntentClassifier
from app.orchestrator.models import IntentClassification, ProposedToolCall, Session
from app.orchestrator.service import Orchestrator
from app.rag.generator import LLMError, LLMProvider
from app.rag.models import LLMDraft
from app.rag.service import RagService
from app.tools.base import build_context

pytestmark = pytest.mark.integration


def cls(intent, tools=(), rag="", requires_rag=False, hint="", procedure="", confirm=False, question=""):
    return IntentClassification(
        intent=intent, requires_authentication=True, requires_rag=requires_rag or bool(rag), rag_query=rag,
        requires_tools=bool(tools), tool_calls=[ProposedToolCall(tool=t, arguments_json=json.dumps(a)) for t, a in tools],
        requires_confirmation=confirm, target_hint=hint, procedure=procedure, clarification_question=question)


class ScriptedClassifier(IntentClassifier):
    def __init__(self, *results):
        self.results, self.calls = list(results), 0

    def classify(self, message, session):
        self.calls += 1
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r, "scripted"


class FakeRagLLM(LLMProvider):
    """Answers with the first evidence label it is given."""
    def generate(self, system, user_message):
        label = user_message.split('<evidence id="')[1].split('"')[0]
        return LLMDraft(answer_type="retrieved_fact", answer="Visiting hours are 10:30 to 11:30 and 17:00 to 19:00.",
                        cited_evidence=[label], conflicts=[]), "fake-llm"


def orchestrator(*results):
    return Orchestrator(classifier=ScriptedClassifier(*results), rag=RagService(provider=FakeRagLLM()))


@pytest.fixture(autouse=True)
def frozen():
    with clock.frozen_at(datetime(2026, 11, 18, 9, 0)):
        yield


@pytest.fixture
def rahul():
    return Session(ctx=build_context("rahul.sharma"))


@pytest.fixture
def restore_a5001():
    with connect() as conn:
        orig = conn.execute("SELECT slot_id, doctor_id, status FROM appointments WHERE appointment_id='A5001'").fetchone()
    yield orig
    with connect() as conn:
        cur = conn.execute("SELECT slot_id FROM appointments WHERE appointment_id='A5001'").fetchone()
        conn.execute("UPDATE appointment_slots SET is_available = true WHERE slot_id = %s", (cur["slot_id"],))
        conn.execute("UPDATE appointments SET slot_id=%s, doctor_id=%s, status=%s WHERE appointment_id='A5001'",
                     (orig["slot_id"], orig["doctor_id"], orig["status"]))
        conn.execute("UPDATE appointment_slots SET is_available = false WHERE slot_id = %s", (orig["slot_id"],))
        conn.execute("DELETE FROM pending_actions WHERE patient_id = 'P1001'")


def a5001_slot():
    with connect() as conn:
        return conn.execute("SELECT slot_id, status FROM appointments WHERE appointment_id='A5001'").fetchone()


# ---------------------------------------------------------------- pathways
def test_knowledge_query_goes_through_rag(rahul):
    r = orchestrator(cls("knowledge_query", rag="What are the ICU visiting hours?")).handle(rahul, "ICU visiting hours?")
    assert r.answer_type == "retrieved_fact" and r.citations[0]["document_id"] == "DOC012"
    assert [t["tool"] for t in r.tools_invoked] == ["rag"]


def test_personal_query_uses_tool_with_system_citation(rahul):
    r = orchestrator(cls("lab_query", [("get_lab_results", {"test_name": "HbA1c", "limit": 1})])) \
        .handle(rahul, "What is my latest HbA1c?")
    assert r.answer_type == "calculated" and "7.2 %" in r.answer
    assert {"system": "Laboratory Information System"} in r.citations
    assert r.tools_invoked[0]["tool"] == "get_lab_results"


def test_mixed_query_combines_tool_and_documents(rahul):
    r = orchestrator(cls("mixed", [("get_lab_results", {"test_name": "HbA1c", "limit": 1})],
                         rag="What are the ICU visiting hours?")).handle(rahul, "my HbA1c and ICU hours")
    assert r.answer_type == "calculated"
    assert any(c.get("document_id") == "DOC012" for c in r.citations) and "7.2 %" in r.answer


# ---------------------------------------------------------------- backend validation of the proposal
def test_off_intent_write_tool_is_dropped(rahul, restore_a5001):
    r = orchestrator(cls("lab_query", [("get_lab_results", {"test_name": "HbA1c"}),
                                       ("cancel_appointment", {"appointment_id": "A5001"})])).handle(rahul, "HbA1c")
    assert any("cancel_appointment" in n and "not allowed" in n for n in r.routing_notes)
    assert [t["tool"] for t in r.tools_invoked] == ["get_lab_results"]
    assert a5001_slot()["status"] == "scheduled"


def test_write_always_requires_confirmation(rahul, restore_a5001):
    r = orchestrator(cls("appointment_change", [("cancel_appointment", {"appointment_id": "A5001"})], confirm=False)) \
        .handle(rahul, "cancel my cardiology appointment")
    assert r.requires_confirmation and r.confirmation_id and r.answer_type == "clarification_needed"
    assert a5001_slot()["status"] == "scheduled"


def test_admin_cannot_use_patient_tools():
    r = orchestrator(cls("appointment_query", [("get_appointments", {})])) \
        .handle(Session(ctx=build_context("admin_01")), "show appointments")
    assert r.answer_type == "refused"


def test_staff_must_select_patient_first():
    r = orchestrator(cls("lab_query", [("get_lab_results", {"test_name": "INR"})])) \
        .handle(Session(ctx=build_context("nurse.deepak")), "latest INR")
    assert r.answer_type == "clarification_needed" and "select" in r.answer


def test_classifier_failure_is_handled(rahul):
    r = orchestrator(LLMError("network")).handle(rahul, "hello?")
    assert r.answer_type == "not_found" and "temporarily unavailable" in r.answer


# ---------------------------------------------------------------- deterministic safety (classifier never called)
def test_emergency_bypasses_llm(rahul):
    o = orchestrator()
    r = o.handle(rahul, "My father has severe chest pain right now, what do I do?")
    assert o.classifier.calls == 0 and r.answer_type == "refused" and r.escalation["required"]
    assert any(c.get("document_id") == "DOC010" for c in r.citations)


def test_other_patient_id_refused(rahul):
    o = orchestrator()
    r = o.handle(rahul, "Show me the lab results of P1003")
    assert o.classifier.calls == 0 and r.answer_type == "refused" and "own records" in r.answer


def test_medical_advice_refused(rahul):
    r = orchestrator(cls("medical_advice")).handle(rahul, "Should I double my metformin dose?")
    assert r.answer_type == "refused" and "medical advice" in r.answer


# ---------------------------------------------------------------- action workflow over several turns
def test_reschedule_conversation_end_to_end(rahul, restore_a5001):
    o = orchestrator(cls("appointment_change", hint="cardiology"))
    r1 = o.handle(rahul, "Reschedule my cardiology appointment")
    assert r1.answer_type == "clarification_needed" and "1." in r1.answer and rahul.offered_slots
    chosen = rahul.offered_slots["slots"][1]

    r2 = o.handle(rahul, "2")                                          # option picked deterministically
    assert r2.requires_confirmation and r2.confirmation_id and "Nothing has been changed" in r2.answer
    assert a5001_slot()["slot_id"] == restore_a5001["slot_id"]          # still unchanged

    r3 = o.handle(rahul, "yes")                                        # the USER's yes executes
    assert r3.answer_type == "action_completed" and "verified" in r3.answer
    assert a5001_slot()["slot_id"] == chosen["slot_id"]
    assert rahul.pending_confirmation is None and o.classifier.calls == 1


def test_decline_changes_nothing(rahul, restore_a5001):
    o = orchestrator(cls("appointment_change", [("cancel_appointment", {"appointment_id": "A5001"})], confirm=True))
    o.handle(rahul, "cancel my cardiology appointment")
    r = o.handle(rahul, "no")
    assert "nothing was changed" in r.answer and a5001_slot()["status"] == "scheduled"


def test_ordinal_words_pick_offered_slot(rahul, restore_a5001):
    o = orchestrator(cls("appointment_change", hint="cardiology"))
    o.handle(rahul, "Reschedule my cardiology appointment")
    first = rahul.offered_slots["slots"][0]
    r = o.handle(rahul, "the first one")
    assert r.requires_confirmation and r.confirmation_id
    with connect() as conn:
        params = conn.execute("SELECT params FROM pending_actions WHERE confirmation_id = %s",
                              (r.confirmation_id,)).fetchone()["params"]
    assert params == {"appointment_id": "A5001", "slot_id": first["slot_id"]}
    assert o.classifier.calls == 1                                       # no second LLM call


def test_insurance_without_date_uses_scheduled_surgery(rahul):
    r = orchestrator(cls("insurance_query", procedure="knee replacement")) \
        .handle(rahul, "Am I covered for knee replacement under my insurance plan?")
    assert r.answer_type == "calculated" and "26 Nov 2026" in r.answer
    assert any("no date needed" in n for n in r.routing_notes)


def test_slot_that_was_not_offered_is_rejected(rahul, restore_a5001):
    r = orchestrator(cls("appointment_change", [("reschedule_appointment", {"appointment_id": "A5001", "slot_id": 1})])) \
        .handle(rahul, "move it to slot 1")
    assert r.answer_type == "clarification_needed" and not r.requires_confirmation
    assert rahul.pending_confirmation is None


# ---------------------------------------------------------------- the brief's multi-step scenario
def test_surgery_readiness_plan(rahul):
    r = orchestrator(cls("surgery_readiness")).handle(rahul, "Can I proceed with surgery next week?")
    assert [s["name"] for s in r.plan] == ["get_surgeon_availability", "get_preoperative_test_status",
                                          "check_insurance_eligibility", "get_readiness_checklist"]
    assert r.plan[2]["arguments"]["service_date"] == "2026-11-26"      # insurance checked for the surgery date
    assert r.answer_type == "calculated" and "Still open (7)" in r.answer
    assert "pre-authorisation" in r.answer.lower() and "not medical clearance" in r.answer
    assert r.escalation["required"] and "anaesthesiologist" in r.escalation["to"]
    docs = {c.get("document_id") for c in r.citations}
    assert {"DOC003", "DOC007"} <= docs
