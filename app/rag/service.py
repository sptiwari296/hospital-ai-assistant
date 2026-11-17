"""The RAG service: question in, grounded and cited answer out.

  preprocess -> hybrid retrieve (+filters) -> rerank -> select / not_found gate
  -> build context -> LLM (structured output) -> validate citations & grounding
  -> resolve conflicts (R4) -> citations from metadata -> RagAnswer
"""
import time
import uuid

from app.config import Settings, get_settings
from app.ingestion.embeddings import Embedder
from app.rag.context import SYSTEM_PROMPT, build_user_message
from app.rag.generator import LLMProvider, get_llm_provider
from app.rag.models import NOT_FOUND_MESSAGE, Citation, Evidence, RagAnswer, RetrievalFilters
from app.rag.query import preprocess
from app.rag.reranker import Reranker, rerank
from app.rag.retriever import hybrid_search
from app.rag.selection import select_evidence
from app.rag.validator import validate_draft


FANCY_HYPHENS = str.maketrans({"‐": "-", "‑": "-", "‒": "-", " ": " ", " ": " "})


def clean_answer(text: str) -> str:
    """LLMs often emit non-breaking hyphens/spaces that some terminals and UIs drop."""
    return text.translate(FANCY_HYPHENS).strip()


def citations_from(evidence: list[Evidence]) -> list[Citation]:
    seen, out = set(), []
    for e in evidence:
        key = (e.document_id, e.section)
        if key not in seen:
            seen.add(key)
            out.append(Citation(document_id=e.document_id, document_name=e.document_name, title=e.title,
                                section=e.section, section_title=e.section_title, version=e.version,
                                effective_date=e.effective_date, page=e.page_start))
    return out


class RagService:
    def __init__(self, provider: LLMProvider | None = None, reranker: Reranker | None = None,
                 embedder: Embedder | None = None, settings: Settings | None = None):
        self._provider = provider
        self._reranker = reranker
        self._embedder = embedder
        self.settings = settings or get_settings()

    @property
    def provider(self) -> LLMProvider:
        if self._provider is None:
            self._provider = get_llm_provider()
        return self._provider

    def answer(self, question: str, filters: RetrievalFilters | None = None,
               trace_id: str | None = None) -> RagAnswer:
        started = time.perf_counter()
        trace_id = trace_id or f"HSP-{uuid.uuid4().hex[:8].upper()}"
        query = preprocess(question)

        candidates = hybrid_search(query, filters, self._embedder)
        ranked = rerank(query.normalized, candidates, self._reranker)
        selection = select_evidence(ranked, self.settings)
        considered = [{"chunk_id": e.chunk_id, "label": e.label or None,
                       "rerank": round(e.rerank_score, 2) if e.rerank_score is not None else None,
                       "similarity": round(e.vector_similarity, 3)} for e in ranked[:10]]

        def done(**fields) -> RagAnswer:
            return RagAnswer(trace_id=trace_id, evidence_considered=considered,
                             latency_ms=int((time.perf_counter() - started) * 1000), **fields)

        if not selection.passed_gate:                      # mandatory rule 4, without calling the LLM
            return done(answer=NOT_FOUND_MESSAGE, answer_type="not_found", explanation=selection.reason)

        feedback, all_errors, model = None, [], None
        for _attempt in range(2):                          # one retry with the validator's feedback
            draft, model = self.provider.generate(SYSTEM_PROMPT,
                                                  build_user_message(query.normalized, selection.evidence, feedback))
            check = validate_draft(draft, selection.evidence, query.normalized)
            if check.ok:
                break
            all_errors += check.errors
            feedback = "; ".join(check.errors)
        else:                                              # still invalid -> fail closed
            return done(answer=NOT_FOUND_MESSAGE, answer_type="not_found", model=model,
                        validation_errors=all_errors,
                        explanation="An answer was drafted but could not be verified against the sources.")

        if check.final_answer_type == "not_found":
            return done(answer=NOT_FOUND_MESSAGE, answer_type="not_found", model=model,
                        validation_errors=all_errors,
                        explanation="The retrieved documents do not contain the answer.")

        explanation = {
            "retrieved_fact": "Answered from approved hospital documents that are currently in force.",
            "conflict_flagged": "Approved documents give different instructions and the policy hierarchy "
                                "cannot decide between them. Please confirm with the department.",
            "clarification_needed": "The question needs more detail before it can be answered.",
        }[check.final_answer_type]
        for c in check.conflicts:
            if c.resolution == "resolved":
                explanation += f" Conflict on {c.topic}: {c.winner_document_id} applies ({c.rule})."

        citations = citations_from(check.cited) if check.final_answer_type != "clarification_needed" else []
        return done(answer=clean_answer(draft.answer), answer_type=check.final_answer_type, citations=citations,
                    conflicts=check.conflicts, explanation=explanation, model=model,
                    validation_errors=all_errors)
