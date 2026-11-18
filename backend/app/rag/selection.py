"""Step 5b — Evidence selection and the not_found gate (mandatory rule 4).

A chunk counts as relevant evidence if EITHER signal is strong:
  * cross-encoder rerank score >= MIN_RERANK_SCORE   (precise, but weak on long list-style chunks)
  * vector similarity        >= MIN_VECTOR_SIMILARITY (catches those long chunks)
If NO chunk qualifies, the answer is not_found and the LLM is never called —
it cannot hallucinate an answer it was never asked to write.

Higher-authority documents (government advisory, clinical guideline) that qualify are
always kept, even if they rank lower, because they can override hospital policy (R4).
"""
from dataclasses import dataclass

from app.config import Settings
from app.rag.models import Evidence

HIGH_AUTHORITY = 2  # authority levels 1 and 2


@dataclass
class Selection:
    evidence: list[Evidence]
    passed_gate: bool
    reason: str


def is_relevant(ev: Evidence, settings: Settings) -> bool:
    return (ev.rerank_score is not None and ev.rerank_score >= settings.min_rerank_score) \
        or ev.vector_similarity >= settings.min_vector_similarity


def select_evidence(ranked: list[Evidence], settings: Settings) -> Selection:
    relevant = [e for e in ranked if is_relevant(e, settings)]
    if not relevant:
        best = ranked[0] if ranked else None
        detail = (f"best chunk {best.chunk_id}: rerank={best.rerank_score:.2f}, "
                  f"similarity={best.vector_similarity:.3f}") if best else "no candidates"
        return Selection([], False, f"no evidence above relevance thresholds ({detail})")

    chosen = relevant[: settings.max_evidence]
    for ev in relevant[settings.max_evidence:]:            # authority boost
        if ev.authority_level <= HIGH_AUTHORITY:
            chosen.append(ev)
    for i, ev in enumerate(chosen, start=1):
        ev.label = f"E{i}"
    return Selection(chosen, True, f"{len(chosen)} evidence chunks selected")
