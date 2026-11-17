"""The ingestion pipeline: one function that runs all steps for one uploaded file.

    validate -> extract -> clean -> chunk -> embed -> store -> version control -> audit

It is a plain function that works while the app is running, so the Phase 6
admin upload endpoint can call it directly: no restart needed (R11).
"""
import shutil
import time
import uuid
from pathlib import Path

from app.config import get_settings
from app.db import connect
from app.ingestion import store
from app.ingestion.chunker import chunk_document
from app.ingestion.clean import clean_blocks
from app.ingestion.embeddings import Embedder, get_embedder
from app.ingestion.extract import extract_blocks
from app.ingestion.models import DocumentMetadata, IngestionReport
from app.ingestion.validation import ValidationError, validate_file
from app.injection import scan


def ingest_document(file_path: str | Path, meta: DocumentMetadata, uploaded_by: str,
                    embedder: Embedder | None = None) -> IngestionReport:
    settings = get_settings()
    file_path = Path(file_path)
    trace_id = f"ING-{uuid.uuid4().hex[:8].upper()}"
    started = time.perf_counter()
    steps: list[str] = []

    with connect() as conn:
        user = store.get_user(conn, uploaded_by)
        user_id = user["user_id"] if user else None
        role = user["role"] if user else None

        def finish(status: str, message: str = "", chunks: int = 0, superseded: list[str] | None = None,
                   model: str | None = None) -> IngestionReport:
            latency = int((time.perf_counter() - started) * 1000)
            store.write_audit(conn, trace_id=trace_id, user_id=user_id, role=role, file_name=file_path.name,
                              document_id=meta.document_id, status=status, message=message, steps=steps,
                              model_name=model, latency_ms=latency)
            conn.commit()
            return IngestionReport(trace_id=trace_id, document_id=meta.document_id, status=status,
                                   chunk_count=chunks, superseded=superseded or [], message=message,
                                   latency_ms=latency)

        # --- Authorisation: only document administrators may change the knowledge base
        if not user or not user["is_active"] or role != "doc_admin":
            return finish("rejected", f"user '{uploaded_by}' is not an active document administrator")
        steps.append("authorised")

        # --- Step 1: validation
        try:
            checksum = validate_file(file_path, settings)
        except ValidationError as exc:
            return finish("rejected", f"validation failed: {exc}")
        if file_path.suffix.lower() != Path(meta.document_name).suffix.lower():
            return finish("rejected", "file type does not match metadata document_name")
        steps.append("validated")

        # --- Steps 2–3: extract and clean
        blocks = clean_blocks(extract_blocks(file_path))
        steps.append(f"extracted_{len(blocks)}_blocks")

        # --- Security: prompt-injection scan (R8). Suspicious documents are quarantined as drafts:
        # stored for review, but never searchable until an administrator approves them.
        quarantine = ""
        if (found := scan("\n".join(b.text for b in blocks))) and meta.approval_status == "approved":
            meta = meta.model_copy(update={"approval_status": "draft"})
            quarantine = f"QUARANTINED as draft for review - instruction-like text found: {found}"
            steps.append("quarantined")

        existing = store.find_document_by_checksum(conn, checksum)
        if existing and existing != meta.document_id:
            return finish("duplicate", f"identical file already stored as {existing}")
        if existing == meta.document_id and store.chunk_count(conn, meta.document_id) > 0:
            # Same file again: only metadata may have changed (e.g. status) -> no re-embedding
            with conn.transaction():
                store.upsert_document(conn, meta, _relative(file_path, settings), checksum)
                superseded = store.refresh_lineage(conn, meta.lineage_id)
            steps.append("metadata_updated")
            return finish("unchanged", quarantine or "content unchanged; metadata refreshed", superseded=superseded)

        # --- Step 4: chunk
        chunks = chunk_document(blocks, meta.title, settings.chunk_max_tokens,
                                settings.chunk_overlap_tokens, settings.min_preamble_tokens)
        if not chunks:
            return finish("rejected", "no extractable text (scanned PDF? OCR is not supported yet)")
        steps.append(f"chunked_{len(chunks)}")

        # --- Step 6: embeddings
        embedder = embedder or get_embedder()
        vectors = embedder.embed_documents([c.embed_text for c in chunks])
        steps.append("embedded")

        # --- Steps 5, 7, 8: store everything in ONE transaction (all or nothing)
        stored_path = _store_file(file_path, meta, settings)
        with conn.transaction():
            store.upsert_document(conn, meta, stored_path, checksum)
            store.replace_chunks(conn, meta.document_id, chunks, vectors)
            superseded = store.refresh_lineage(conn, meta.lineage_id)
        steps.append("indexed")
        msg = "; ".join(m for m in (quarantine, f"superseded {', '.join(superseded)}" if superseded else "") if m)
        return finish("ingested", msg, chunks=len(chunks), superseded=superseded, model=embedder.name)


def _store_file(file_path: Path, meta: DocumentMetadata, settings) -> str:
    """Keep a copy of every uploaded file in the knowledge-base folder (old versions are retained)."""
    target = settings.storage_dir / meta.document_name
    if file_path.resolve() != target.resolve():
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, target)
    return _relative(target, settings)


def _relative(path: Path, settings) -> str:
    try:
        return str(path.resolve().relative_to(settings.storage_dir.resolve().parents[2])).replace("\\", "/")
    except ValueError:
        return str(path)
