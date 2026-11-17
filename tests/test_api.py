"""Phase 6 tests: the HTTP API end to end (FastAPI TestClient, real database, scripted LLM)."""
import json
import os
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import clock
from app.api.main import app
from app.api.routes.chat import get_orchestrator
from app.config import get_settings
from app.db import connect
from app.orchestrator.classifier import IntentClassifier
from app.orchestrator.models import IntentClassification, ProposedToolCall
from app.orchestrator.service import Orchestrator
from app.rag.generator import LLMProvider
from app.rag.models import LLMDraft
from app.rag.service import RagService

pytestmark = pytest.mark.integration
get_settings()
PASSWORD = os.environ["DEMO_PASSWORD"]


def cls(intent, tools=(), rag="", hint=""):
    return IntentClassification(
        intent=intent, requires_authentication=True, requires_rag=bool(rag), rag_query=rag, requires_tools=bool(tools),
        tool_calls=[ProposedToolCall(tool=t, arguments_json=json.dumps(a)) for t, a in tools],
        requires_confirmation=False, target_hint=hint, procedure="", clarification_question="")


class RuleClassifier(IntentClassifier):
    """Deterministic stand-in for the LLM classifier, keyed on words in the message."""
    def classify(self, message, session):
        m = message.lower()
        if "icu" in m:
            return cls("knowledge_query", rag="What are the ICU visiting hours?"), "rules"
        if "reschedule" in m:
            return cls("appointment_change", hint="cardiology"), "rules"
        if "hba1c" in m:
            return cls("lab_query", [("get_lab_results", {"test_name": "HbA1c", "limit": 1})]), "rules"
        return cls("out_of_scope"), "rules"


class FakeRagLLM(LLMProvider):
    def generate(self, system, user_message):
        label = user_message.split('<evidence id="')[1].split('"')[0]
        return LLMDraft(answer_type="retrieved_fact", answer="Visiting hours are 10:30 to 11:30 and 17:00 to 19:00.",
                        cited_evidence=[label], conflicts=[]), "fake-llm"


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides[get_orchestrator] = lambda: Orchestrator(RuleClassifier(), RagService(provider=FakeRagLLM()))
    with clock.frozen_at(datetime(2026, 11, 18, 9, 0)), TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def login(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def restore_a5001():
    with connect() as conn:
        orig = conn.execute("SELECT slot_id, doctor_id, status FROM appointments WHERE appointment_id='A5001'").fetchone()
    yield
    with connect() as conn:
        cur = conn.execute("SELECT slot_id FROM appointments WHERE appointment_id='A5001'").fetchone()
        conn.execute("UPDATE appointment_slots SET is_available = true WHERE slot_id = %s", (cur["slot_id"],))
        conn.execute("UPDATE appointments SET slot_id=%s, doctor_id=%s, status=%s WHERE appointment_id='A5001'",
                     (orig["slot_id"], orig["doctor_id"], orig["status"]))
        conn.execute("UPDATE appointment_slots SET is_available = false WHERE slot_id = %s", (orig["slot_id"],))
        conn.execute("DELETE FROM pending_actions WHERE patient_id = 'P1001'")


# ---------------------------------------------------------------- auth
def test_login_failures_look_identical(client):
    wrong = client.post("/api/auth/login", json={"username": "rahul.sharma", "password": "nope"})
    unknown = client.post("/api/auth/login", json={"username": "nobody", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401 and wrong.json() == unknown.json()


def test_token_required_and_verified(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer forged.token.value"}).status_code == 401
    me = client.get("/api/me", headers=login(client, "nurse.anita")).json()
    assert me["role"] == "staff" and [p["patient_id"] for p in me["assigned_patients"]] == ["P1001", "P1004"]


# ---------------------------------------------------------------- chat
def test_chat_answer_has_standard_shape_and_is_audited(client):
    h = login(client, "rahul.sharma")
    r = client.post("/api/chat", json={"message": "What are the ICU visiting hours?"}, headers=h).json()
    assert {"trace_id", "answer", "answer_type", "citations", "tools_invoked", "explanation", "session_id"} <= set(r)
    assert r["answer_type"] == "retrieved_fact" and r["citations"][0]["document_id"] == "DOC012"
    trace = client.get(f"/api/audit/{r['trace_id']}", headers=h).json()
    assert trace["intent"] == "knowledge_query" and trace["patient_accessed"] == "P1001"
    assert client.get(f"/api/audit/{r['trace_id']}", headers=login(client, "priya.nair")).status_code == 404
    assert client.get(f"/api/audit/{r['trace_id']}", headers=login(client, "sysadmin_01")).status_code == 200


def test_session_belongs_to_its_user(client):
    sid = client.post("/api/chat", json={"message": "latest HbA1c"}, headers=login(client, "rahul.sharma")).json()["session_id"]
    assert client.post("/api/chat", json={"message": "latest HbA1c", "session_id": sid},
                       headers=login(client, "priya.nair")).status_code == 404


def test_reschedule_with_confirm_button(client, restore_a5001):
    h = login(client, "rahul.sharma")
    r1 = client.post("/api/chat", json={"message": "Reschedule my cardiology appointment"}, headers=h).json()
    sid = r1["session_id"]
    r2 = client.post("/api/chat", json={"message": "2", "session_id": sid}, headers=h).json()
    assert r2["requires_confirmation"] and r2["confirmation_id"]
    wrong = client.post("/api/chat", json={"session_id": sid, "decision": "confirm", "confirmation_id": "CNF-FAKE"},
                        headers=h)
    assert wrong.status_code == 409
    r3 = client.post("/api/chat", json={"session_id": sid, "decision": "confirm",
                                        "confirmation_id": r2["confirmation_id"]}, headers=h).json()
    assert r3["answer_type"] == "action_completed" and "verified" in r3["answer"]


# ---------------------------------------------------------------- patient endpoints
def test_profile_is_masked(client):
    p = client.get("/api/patient/profile", headers=login(client, "rahul.sharma")).json()
    assert p["patient_id"] == "P1001" and p["national_id"] == "****-****-1001"   # only the last 4 digits
    assert p["insurance"]["policy_number"] == "NWG-****-**4455"                  # insurer code is not sensitive
    assert "114455" not in json.dumps(p)


def test_staff_scope_and_admin_blocked(client):
    nurse = login(client, "nurse.anita")
    assert client.get("/api/appointments?patient_id=P1001", headers=nurse).status_code == 200
    assert client.get("/api/appointments?patient_id=P1003", headers=nurse).status_code == 403
    assert client.get("/api/appointments", headers=login(client, "admin_01")).status_code == 403


def test_labs_insurance_and_reschedule_proposal(client, restore_a5001):
    h = login(client, "rahul.sharma")
    overview = client.get("/api/labs", headers=h).json()
    assert {r["test_code"] for r in overview["latest_by_test"]} >= {"HBA1C", "FBS"}
    assert client.get("/api/labs?test=HbA1c&limit=1", headers=h).json()["data"]["latest"]["value"] == 7.2
    ins = client.post("/api/insurance/verify", json={"procedure": "knee replacement"}, headers=h).json()
    assert ins["data"]["eligibility_status"] == "requires_pre_authorization"
    slot = client.get("/api/appointments/A5001/slots", headers=h).json()["data"]["slots"][0]["slot_id"]
    proposal = client.post("/api/appointments/reschedule", json={"appointment_id": "A5001", "slot_id": slot},
                           headers=h).json()
    assert proposal["status"] == "requires_confirmation"
    declined = client.post(f"/api/actions/{proposal['data']['confirmation_id']}/decline", headers=h).json()
    assert "nothing was changed" in declined["summary"]


# ---------------------------------------------------------------- documents (R11)
@pytest.fixture
def cleanup_test_docs():
    yield
    with connect() as conn:
        conn.execute("DELETE FROM documents WHERE lineage_id = 'TEST_API_PARKING'")
        conn.execute("DELETE FROM ingestion_jobs WHERE file_name LIKE 'parking_policy%%'")
        conn.execute("DELETE FROM audit_logs WHERE query_redacted LIKE 'parking_policy%%'")
    for p in get_settings().storage_dir.glob("parking_policy*"):
        p.unlink()


FORM = {"title": "Visitor Parking Policy", "department": "Facilities", "document_type": "hospital_sop",
        "lineage_id": "TEST_API_PARKING", "version": "1.0", "effective_date": "2026-01-01", "authority_level": "4"}


def test_only_doc_admin_can_upload(client):
    r = client.post("/api/documents/upload", headers=login(client, "rahul.sharma"), data=FORM,
                    files={"file": ("parking_policy.txt", b"# Parking\n\n1. Hours\nOpen all day.\n", "text/plain")})
    assert r.status_code == 403


def test_live_upload_is_searchable_without_restart(client, cleanup_test_docs):
    h = login(client, "admin_01")
    text = b"# Visitor Parking Policy\n\n1. Hours\nThe visitor car park is open 24 hours.\n\n2. Fees\nParking is free for the first 2 hours.\n"
    r = client.post("/api/documents/upload", headers=h, data=FORM, files={"file": ("parking_policy.txt", text, "text/plain")})
    assert r.status_code == 202
    job = client.get(f"/api/documents/jobs/{r.json()['job_id']}", headers=h).json()  # background task already ran
    assert job["status"] == "ingested" and job["chunk_count"] == 2
    docs = {d["document_id"]: d for d in client.get("/api/documents", headers=h).json()}
    assert docs[r.json()["document_id"]]["searchable"] is True

    retired = client.patch(f"/api/documents/{r.json()['document_id']}/status", headers=h,
                           json={"approval_status": "retired"})
    assert retired.status_code == 200
    docs = {d["document_id"]: d for d in client.get("/api/documents", headers=h).json()}
    assert docs[r.json()["document_id"]]["searchable"] is False


def test_bad_upload_rejected(client, cleanup_test_docs):
    h = login(client, "admin_01")
    bad_meta = client.post("/api/documents/upload", headers=h, data={**FORM, "version": "one"},
                           files={"file": ("parking_policy.txt", b"x", "text/plain")})
    assert bad_meta.status_code == 422
    fake_pdf = client.post("/api/documents/upload", headers=h, data=FORM,
                           files={"file": ("parking_policy.pdf", b"not really a pdf", "application/pdf")})
    job = client.get(f"/api/documents/jobs/{fake_pdf.json()['job_id']}", headers=h).json()
    assert job["status"] == "rejected" and "does not match" in job["message"]
