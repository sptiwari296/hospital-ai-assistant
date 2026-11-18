"""Quick semantic search over the CURRENT knowledge base (Phase 3 builds the real RAG).

Run:  .venv\\Scripts\\python scripts\\search_demo.py "What are ICU visiting hours?"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.db import connect  # noqa: E402
from app.ingestion.embeddings import get_embedder  # noqa: E402


def search(question: str, k: int = 3) -> list[dict]:
    vector = get_embedder().embed_query(question)
    with connect() as conn:
        return conn.execute(
            """
            SELECT document_id, version, effective_date, section, section_title, page_start, content,
                   1 - (embedding <=> %s) AS similarity      -- <=> is cosine distance
            FROM searchable_chunks
            ORDER BY embedding <=> %s
            LIMIT %s
            """,
            (vector, vector, k),
        ).fetchall()


if __name__ == "__main__":
    questions = sys.argv[1:] or ["What are ICU visiting hours?"]
    for q in questions:
        print(f"\nQ: {q}")
        for r in search(q):
            print(f"  {r['similarity']:.3f}  {r['document_id']} v{r['version']} (eff. {r['effective_date']}) "
                  f"§{r['section']} {r['section_title']} p{r['page_start']}")
            print(f"         {r['content'].replace(chr(10), ' / ')[:140]}")
