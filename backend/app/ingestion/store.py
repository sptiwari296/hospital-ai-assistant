"""Steps 7–8 — Storage in PostgreSQL/pgvector and version control."""
import json

import numpy as np
import psycopg

from app.ingestion.models import Chunk, DocumentMetadata


def get_user(conn: psycopg.Connection, auth_subject: str) -> dict | None:
    return conn.execute(
        "SELECT user_id, role, is_active FROM users WHERE auth_subject = %s", (auth_subject,)
    ).fetchone()


def find_document_by_checksum(conn: psycopg.Connection, checksum: str) -> str | None:
    row = conn.execute("SELECT document_id FROM documents WHERE checksum = %s", (checksum,)).fetchone()
    return row["document_id"] if row else None


def chunk_count(conn: psycopg.Connection, document_id: str) -> int:
    return conn.execute(
        "SELECT count(*) AS n FROM document_chunks WHERE document_id = %s", (document_id,)
    ).fetchone()["n"]


def upsert_document(conn: psycopg.Connection, meta: DocumentMetadata, file_path: str, checksum: str) -> None:
    conn.execute(
        """
        INSERT INTO documents (document_id, document_name, title, department, document_type, lineage_id,
                               version, effective_date, expiry_date, approval_status, authority_level,
                               uploaded_by, file_path, checksum)
        VALUES (%(document_id)s, %(document_name)s, %(title)s, %(department)s, %(document_type)s, %(lineage_id)s,
                %(version)s, %(effective_date)s, %(expiry_date)s, %(approval_status)s, %(authority_level)s,
                %(uploaded_by)s, %(file_path)s, %(checksum)s)
        ON CONFLICT (document_id) DO UPDATE SET
            document_name = EXCLUDED.document_name, title = EXCLUDED.title, department = EXCLUDED.department,
            document_type = EXCLUDED.document_type, lineage_id = EXCLUDED.lineage_id, version = EXCLUDED.version,
            effective_date = EXCLUDED.effective_date, expiry_date = EXCLUDED.expiry_date,
            approval_status = EXCLUDED.approval_status, authority_level = EXCLUDED.authority_level,
            uploaded_by = EXCLUDED.uploaded_by, file_path = EXCLUDED.file_path, checksum = EXCLUDED.checksum,
            uploaded_at = now()
        """,
        {**meta.model_dump(), "file_path": file_path, "checksum": checksum},
    )


def replace_chunks(conn: psycopg.Connection, document_id: str, chunks: list[Chunk], vectors: list[np.ndarray]) -> None:
    """Delete old chunks of this document and insert the new ones (re-ingestion is safe)."""
    conn.execute("DELETE FROM document_chunks WHERE document_id = %s", (document_id,))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO document_chunks (chunk_id, document_id, chunk_index, section, section_title, heading_path,
                                         page_start, page_end, content, token_count, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (f"{document_id}-c{c.chunk_index:03d}", document_id, c.chunk_index, c.section, c.section_title,
                 c.heading_path, c.page_start, c.page_end, c.content, c.token_count, vec)
                for c, vec in zip(chunks, vectors, strict=True)
            ],
        )


def refresh_lineage(conn: psycopg.Connection, lineage_id: str) -> list[str]:
    """Version control: among the approved versions of one policy that are in force today,
    keep the newest and mark the others 'superseded'. Old rows and chunks are KEPT for audit.
    Future-dated versions stay 'approved' and switch on automatically on their effective date
    (the `searchable_chunks` view checks dates at query time)."""
    rows = conn.execute(
        """
        WITH current AS (
            SELECT document_id FROM documents
            WHERE lineage_id = %(l)s AND approval_status = 'approved' AND effective_date <= CURRENT_DATE
              AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE)
            ORDER BY effective_date DESC, string_to_array(version, '.')::int[] DESC
            LIMIT 1
        )
        UPDATE documents SET approval_status = 'superseded'
        WHERE lineage_id = %(l)s AND approval_status = 'approved' AND effective_date <= CURRENT_DATE
          AND document_id NOT IN (SELECT document_id FROM current)
        RETURNING document_id
        """,
        {"l": lineage_id},
    ).fetchall()
    return [r["document_id"] for r in rows]


def write_audit(conn: psycopg.Connection, *, trace_id: str, user_id: str | None, role: str | None,
                file_name: str, document_id: str, status: str, message: str, steps: list[str],
                model_name: str | None, latency_ms: int) -> None:
    conn.execute(
        """
        INSERT INTO audit_logs (trace_id, user_id, role, action, query_redacted, answer_type,
                                retrieved_sources, tools_invoked, privacy_checks, model_name, latency_ms)
        VALUES (%s, %s, %s, 'document_upload', %s, %s, %s, %s, %s, %s, %s)
        """,
        (trace_id, user_id, role, file_name, status,
         json.dumps([{"document_id": document_id, "message": message}]),
         json.dumps([{"step": s} for s in steps]),
         json.dumps([{"check": "uploader_role_is_doc_admin", "passed": role == "doc_admin"}]),
         model_name, latency_ms),
    )
