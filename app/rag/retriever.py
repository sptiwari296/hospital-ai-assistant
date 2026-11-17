"""Steps 2–4 — Hybrid retrieval with metadata filtering.

Two searches run against `searchable_chunks` (which ALREADY excludes unapproved,
superseded, expired and not-yet-effective documents — mandatory rules 1 and 2):
  * vector search  — finds chunks with the same MEANING
  * keyword search — finds exact terms (HbA1c, PT/INR, Code Blue, policy names)
Their rankings are merged with Reciprocal Rank Fusion (RRF):
    score(chunk) = sum over searches of 1 / (60 + rank)
RRF only uses ranks, so the two very different score scales never need to be compared.
"""
from app.config import get_settings
from app.db import connect
from app.ingestion.embeddings import Embedder, get_embedder
from app.rag.models import Evidence, RetrievalFilters
from app.rag.query import ProcessedQuery

COLUMNS = """chunk_id, document_id, document_name, title, department, document_type, lineage_id, version,
             effective_date, authority_level, section, section_title, heading_path, page_start, content"""


def _filter_sql(filters: RetrievalFilters | None) -> tuple[str, dict]:
    clauses, params = [], {}
    if filters and filters.departments:
        clauses.append("department = ANY(%(departments)s)")
        params["departments"] = filters.departments
    if filters and filters.document_types:
        clauses.append("document_type = ANY(%(document_types)s)")
        params["document_types"] = filters.document_types
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


def hybrid_search(query: ProcessedQuery, filters: RetrievalFilters | None = None,
                  embedder: Embedder | None = None) -> list[Evidence]:
    settings = get_settings()
    embedder = embedder or get_embedder()
    vector = embedder.embed_query(query.normalized)
    where, params = _filter_sql(filters)
    params |= {"vec": vector, "k": settings.retrieval_k, "kw": query.keyword_text}

    with connect() as conn:
        vector_rows = conn.execute(
            f"""SELECT {COLUMNS}, 1 - (embedding <=> %(vec)s) AS vector_similarity
                FROM searchable_chunks WHERE TRUE {where}
                ORDER BY embedding <=> %(vec)s LIMIT %(k)s""", params).fetchall()
        # OR-match every meaningful word (stop-words removed and stemmed by Postgres)
        keyword_rows = conn.execute(
            f"""WITH q AS (
                    SELECT to_tsquery('english', array_to_string(
                        tsvector_to_array(to_tsvector('english', %(kw)s)), ' | ')) AS tsq)
                SELECT {COLUMNS}, 1 - (embedding <=> %(vec)s) AS vector_similarity
                FROM searchable_chunks, q
                WHERE content_tsv @@ q.tsq {where}
                ORDER BY ts_rank_cd(content_tsv, q.tsq) DESC LIMIT %(k)s""", params).fetchall()

    by_id: dict[str, Evidence] = {}
    for row in [*vector_rows, *keyword_rows]:
        by_id.setdefault(row["chunk_id"], Evidence(**row))
    for rank, row in enumerate(keyword_rows, start=1):
        by_id[row["chunk_id"]].keyword_rank = rank

    fused = reciprocal_rank_fusion([[r["chunk_id"] for r in vector_rows], [r["chunk_id"] for r in keyword_rows]],
                                   k=settings.rrf_k)
    for chunk_id, score in fused.items():
        by_id[chunk_id].rrf_score = score
    return sorted(by_id.values(), key=lambda e: e.rrf_score, reverse=True)
