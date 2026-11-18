"""Ingest every document listed in data/hospital_documents/metadata.json.

Run:  .venv\\Scripts\\python scripts\\ingest_kb.py
Safe to run again: unchanged files are detected by checksum and not re-embedded.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import connect  # noqa: E402
from app.ingestion.models import DocumentMetadata  # noqa: E402
from app.ingestion.pipeline import ingest_document  # noqa: E402


def main() -> None:
    settings = get_settings()
    kb = settings.storage_dir.parent
    entries = json.loads((kb / "metadata.json").read_text(encoding="utf-8"))

    print(f"{'document':<8} {'status':<10} {'chunks':>6} {'ms':>6}  trace / message")
    for entry in entries:
        meta = DocumentMetadata(**entry)
        report = ingest_document(settings.storage_dir / meta.document_name, meta, meta.uploaded_by)
        print(f"{report.document_id:<8} {report.status:<10} {report.chunk_count:>6} {report.latency_ms:>6}  "
              f"{report.trace_id} {report.message}")

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT d.document_id, d.version, d.approval_status,
                   count(c.chunk_id) AS chunks,
                   EXISTS (SELECT 1 FROM searchable_chunks s WHERE s.document_id = d.document_id) AS searchable
            FROM documents d LEFT JOIN document_chunks c USING (document_id)
            GROUP BY d.document_id ORDER BY d.document_id
            """
        ).fetchall()
    print("\nKnowledge base status (searchable = used to answer questions today):")
    for r in rows:
        print(f"  {r['document_id']}  v{r['version']:<4} {r['approval_status']:<11} chunks={r['chunks']:<3} "
              f"searchable={'YES' if r['searchable'] else 'no'}")


if __name__ == "__main__":
    main()
