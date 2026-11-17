"""End-to-end ingestion tests against the real Docker database.

Uses temporary TEST documents (DOC9xx) and deletes them afterwards, so the
real knowledge base is never changed.
"""
from datetime import date, timedelta

import pytest

from app.db import admin_connect as connect  # fixtures need the owner role (bypasses RLS)
from app.ingestion.models import DocumentMetadata
from app.ingestion.pipeline import ingest_document

pytestmark = pytest.mark.integration
TEST_IDS = ("DOC901", "DOC902", "DOC903")


def _meta(doc_id: str, version: str, effective: date, name: str) -> DocumentMetadata:
    return DocumentMetadata(
        document_id=doc_id, document_name=name, title="Test Parking Policy", department="Facilities",
        document_type="hospital_sop", lineage_id="TEST_PARKING", version=version,
        effective_date=effective, approval_status="approved", authority_level=4, uploaded_by="admin_01")


def _txt(tmp_path, name: str, hours: str):
    f = tmp_path / name
    f.write_text(f"# Test Parking Policy\n\n1. Hours\nThe visitor car park is open {hours}.\n", encoding="utf-8")
    return f


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with connect() as conn:
        conn.execute("DELETE FROM documents WHERE document_id = ANY(%s)", (list(TEST_IDS),))
        conn.execute("DELETE FROM audit_logs WHERE query_redacted LIKE 'test_parking%%'")
    from app.config import get_settings
    for p in get_settings().storage_dir.glob("test_parking*"):
        p.unlink()


def _searchable_ids() -> set[str]:
    with connect() as conn:
        rows = conn.execute("SELECT DISTINCT document_id FROM searchable_chunks").fetchall()
    return {r["document_id"] for r in rows}


def test_real_knowledge_base_version_rules():
    ids = _searchable_ids()
    assert "DOC012" in ids          # current ICU policy
    assert "DOC011" not in ids      # superseded ICU policy
    assert "DOC008" not in ids      # insurance v3 not effective until 2027


def test_new_version_supersedes_old_live(tmp_path):
    today = date.today()
    r1 = ingest_document(_txt(tmp_path, "test_parking_v1.txt", "7 am to 9 pm"),
                         _meta("DOC901", "1.0", today - timedelta(days=30), "test_parking_v1.txt"), "admin_01")
    assert r1.status == "ingested" and r1.chunk_count == 1
    assert "DOC901" in _searchable_ids()

    r2 = ingest_document(_txt(tmp_path, "test_parking_v2.txt", "24 hours"),
                         _meta("DOC902", "2.0", today, "test_parking_v2.txt"), "admin_01")
    assert r2.status == "ingested" and r2.superseded == ["DOC901"]
    ids = _searchable_ids()
    assert "DOC902" in ids and "DOC901" not in ids

    with connect() as conn:   # old version is retained for audit, just not searchable
        old = conn.execute("SELECT approval_status FROM documents WHERE document_id='DOC901'").fetchone()
    assert old["approval_status"] == "superseded"


def test_future_version_waits_for_its_date(tmp_path):
    today = date.today()
    ingest_document(_txt(tmp_path, "test_parking_v1.txt", "7 am to 9 pm"),
                    _meta("DOC901", "1.0", today - timedelta(days=30), "test_parking_v1.txt"), "admin_01")
    r = ingest_document(_txt(tmp_path, "test_parking_v3.txt", "6 am to 11 pm"),
                        _meta("DOC903", "3.0", today + timedelta(days=30), "test_parking_v3.txt"), "admin_01")
    assert r.status == "ingested" and r.superseded == []
    ids = _searchable_ids()
    assert "DOC901" in ids and "DOC903" not in ids


def test_reingesting_same_file_is_unchanged(tmp_path):
    f = _txt(tmp_path, "test_parking_v1.txt", "7 am to 9 pm")
    meta = _meta("DOC901", "1.0", date.today(), "test_parking_v1.txt")
    assert ingest_document(f, meta, "admin_01").status == "ingested"
    assert ingest_document(f, meta, "admin_01").status == "unchanged"


def test_non_admin_cannot_upload_and_attempt_is_audited(tmp_path):
    f = _txt(tmp_path, "test_parking_v1.txt", "7 am to 9 pm")
    r = ingest_document(f, _meta("DOC901", "1.0", date.today(), "test_parking_v1.txt"), "rahul.sharma")
    assert r.status == "rejected" and "administrator" in r.message
    with connect() as conn:
        audit = conn.execute("SELECT role, answer_type FROM audit_logs WHERE trace_id = %s", (r.trace_id,)).fetchone()
        stored = conn.execute("SELECT 1 FROM documents WHERE document_id='DOC901'").fetchone()
    assert audit["role"] == "patient" and audit["answer_type"] == "rejected"
    assert stored is None
