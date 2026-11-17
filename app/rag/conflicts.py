"""R4 — Deterministic policy conflict resolution (mandatory rule 5).

The LLM is good at NOTICING that two passages disagree ("4 hours" vs "6 hours").
It is NOT trusted to decide which one wins. That decision uses only database metadata:
  0. not approved / not effective / superseded -> already excluded by searchable_chunks
  1. higher authority wins   (1 government > 2 clinical guideline > 3 hospital policy > 4 dept SOP)
  2. same policy lineage     -> higher version wins
  3. later effective date wins
  4. still tied              -> unresolved -> answer_type = conflict_flagged
"""
from dataclasses import dataclass

from app.rag.models import Evidence


@dataclass
class Resolution:
    winner: Evidence | None
    rule: str


def _version_key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def resolve(items: list[Evidence]) -> Resolution:
    docs = {e.document_id: e for e in items}  # several chunks of one document = one voice
    if len(docs) < 2:
        return Resolution(next(iter(docs.values())), "same document (internal detail, no policy conflict)")
    candidates = list(docs.values())

    best_level = min(e.authority_level for e in candidates)
    top = [e for e in candidates if e.authority_level == best_level]
    if len(top) == 1:
        return Resolution(top[0], f"higher authority (level {best_level}) overrides lower")

    lineages = {e.lineage_id for e in top}
    if len(lineages) == 1:
        newest = max(top, key=lambda e: _version_key(e.version))
        return Resolution(newest, "same policy lineage: newer version applies")

    latest = max(e.effective_date for e in top)
    newest = [e for e in top if e.effective_date == latest]
    if len(newest) == 1:
        return Resolution(newest[0], "same authority: later effective date applies")

    return Resolution(None, "same authority level and same effective date: cannot be resolved automatically")
