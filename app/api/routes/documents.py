"""Knowledge-base administration (doc_admin only) — live ingestion without restart (R11)."""
import re
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ValidationError

from app.api.auth import CurrentUser, require_role
from app.api.monitoring import INGESTION
from app.config import ROOT, get_settings
from app.db import connect
from app.ingestion import store
from app.ingestion.models import DocumentMetadata
from app.ingestion.pipeline import ingest_document

router = APIRouter(prefix="/api/documents")
admin_only = require_role("doc_admin")
UPLOAD_DIR = ROOT / "data" / "uploads"


def _next_document_id(conn) -> str:
    row = conn.execute("SELECT max(substring(document_id FROM 4)::int) AS n FROM documents "
                       "WHERE document_id ~ '^DOC[0-9]+$'").fetchone()
    return f"DOC{(row['n'] or 0) + 1:03d}"


def run_ingestion(job_id: str, path: Path, meta: DocumentMetadata, username: str) -> None:
    """Background task: runs after the HTTP response has been sent."""
    with connect() as conn:
        conn.execute("UPDATE ingestion_jobs SET status = 'running' WHERE job_id = %s", (job_id,))
    try:
        report = ingest_document(path, meta, username)
        status, message, trace, chunks = report.status, report.message, report.trace_id, report.chunk_count
    except Exception as exc:  # never leave a job stuck in 'running'
        status, message, trace, chunks = "failed", f"{type(exc).__name__}: {exc}", None, None
    finally:
        path.unlink(missing_ok=True)  # the pipeline keeps its own copy of accepted files
    with connect() as conn:
        conn.execute("""UPDATE ingestion_jobs SET status = %s, message = %s, trace_id = %s, chunk_count = %s,
                        finished_at = now() WHERE job_id = %s""", (status, message, trace, chunks, job_id))
    INGESTION.labels(status).inc()


@router.post("/upload", status_code=202)
async def upload(background: BackgroundTasks, user: CurrentUser = Depends(admin_only),
                 file: UploadFile = File(...), title: str = Form(...), department: str = Form(...),
                 document_type: str = Form(...), lineage_id: str = Form(...), version: str = Form(...),
                 effective_date: date = Form(...), expiry_date: date | None = Form(None),
                 approval_status: str = Form("approved"), authority_level: int = Form(3),
                 document_id: str | None = Form(None)):
    settings = get_settings()
    data = await file.read(settings.max_upload_bytes + 1)          # never read more than the limit
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"File larger than {settings.max_upload_bytes // (1024 * 1024)} MB")
    safe_name = re.sub(r"[^\w\-. ]", "_", Path(file.filename or "upload").name)[:120]
    with connect() as conn:
        doc_id = (document_id or "").strip().upper() or _next_document_id(conn)
    try:
        meta = DocumentMetadata(document_id=doc_id, document_name=safe_name, title=title, department=department,
                                document_type=document_type, lineage_id=lineage_id.strip().upper(), version=version,
                                effective_date=effective_date, expiry_date=expiry_date,
                                approval_status=approval_status, authority_level=authority_level,
                                uploaded_by=user.auth_subject)
    except ValidationError as exc:
        raise HTTPException(422, [{"field": e["loc"][0], "error": e["msg"]} for e in exc.errors()]) from None

    job_id = f"JOB-{uuid.uuid4().hex[:8].upper()}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{job_id}_{safe_name}"
    path.write_bytes(data)
    with connect() as conn:
        conn.execute("""INSERT INTO ingestion_jobs (job_id, document_id, file_name, uploaded_by, status)
                        VALUES (%s, %s, %s, %s, 'queued')""", (job_id, doc_id, safe_name, user.user_id))
    background.add_task(run_ingestion, job_id, path, meta, user.auth_subject)
    return {"job_id": job_id, "document_id": doc_id, "status": "queued"}


@router.get("")
def list_documents(user: CurrentUser = Depends(admin_only)):
    with connect() as conn:
        return conn.execute(
            """SELECT d.document_id, d.document_name, d.title, d.department, d.document_type, d.lineage_id,
                      d.version, d.effective_date, d.expiry_date, d.approval_status, d.authority_level,
                      d.uploaded_by, d.uploaded_at, count(c.chunk_id) AS chunks,
                      EXISTS (SELECT 1 FROM searchable_chunks s WHERE s.document_id = d.document_id) AS searchable
               FROM documents d LEFT JOIN document_chunks c USING (document_id)
               GROUP BY d.document_id ORDER BY d.lineage_id, d.effective_date DESC""").fetchall()


@router.get("/jobs")
def list_jobs(user: CurrentUser = Depends(admin_only)):
    with connect() as conn:
        return conn.execute("SELECT * FROM ingestion_jobs ORDER BY created_at DESC LIMIT 50").fetchall()


@router.get("/jobs/{job_id}")
def job_status(job_id: str, user: CurrentUser = Depends(admin_only)):
    with connect() as conn:
        job = conn.execute("SELECT * FROM ingestion_jobs WHERE job_id = %s", (job_id,)).fetchone()
    if not job:
        raise HTTPException(404, "Job not found")
    return job


class StatusChange(BaseModel):
    approval_status: str


@router.patch("/{document_id}/status")
def change_status(document_id: str, body: StatusChange, user: CurrentUser = Depends(admin_only)):
    """Approve a draft, or retire a document. Takes effect for the next question — no restart."""
    if body.approval_status not in ("approved", "retired", "draft"):
        raise HTTPException(400, "approval_status must be approved, retired or draft")
    with connect() as conn:
        doc = conn.execute("SELECT lineage_id FROM documents WHERE document_id = %s", (document_id,)).fetchone()
        if not doc:
            raise HTTPException(404, "Document not found")
        conn.execute("UPDATE documents SET approval_status = %s WHERE document_id = %s",
                     (body.approval_status, document_id))
        superseded = store.refresh_lineage(conn, doc["lineage_id"])
    return {"document_id": document_id, "approval_status": body.approval_status, "superseded": superseded}
