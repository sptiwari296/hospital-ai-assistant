"""Unit tests for the RAG building blocks (no database, no models)."""
from datetime import date

from app.config import get_settings
from app.rag.conflicts import resolve
from app.rag.models import Evidence, LLMConflict, LLMDraft
from app.rag.query import preprocess
from app.rag.retriever import reciprocal_rank_fusion
from app.rag.selection import select_evidence
from app.rag.validator import numbers_in, validate_draft


def ev(label, doc, *, authority=3, version="1.0", eff=date(2026, 1, 1), lineage=None, content="",
       rerank=5.0, sim=0.8):
    return Evidence(chunk_id=f"{doc}-c000", document_id=doc, document_name=f"{doc}.pdf", title=doc,
                    department="X", document_type="hospital_sop", lineage_id=lineage or doc, version=version,
                    effective_date=eff, authority_level=authority, section="3", section_title="T",
                    heading_path=doc, page_start=1, content=content, vector_similarity=sim,
                    rerank_score=rerank, label=label)


# ---------- query preprocessing
def test_synonyms_added_for_keyword_search_only():
    q = preprocess("  What's my   blood sugar  limit before TKR? ")
    assert q.normalized == "What's my blood sugar limit before TKR?"
    assert "glucose hba1c" in q.keyword_text and "total knee replacement" in q.keyword_text


# ---------- fusion
def test_rrf_rewards_chunks_found_by_both_searches():
    scores = reciprocal_rank_fusion([["a", "b", "c"], ["c", "d"]], k=60)
    assert max(scores, key=scores.get) == "c"  # rank 3 + rank 1 beats rank 1 alone


# ---------- not_found gate
def test_gate_blocks_when_nothing_relevant():
    s = get_settings()
    sel = select_evidence([ev("", "DOC1", rerank=-11, sim=0.55)], s)
    assert not sel.passed_gate and "no evidence" in sel.reason


def test_gate_accepts_high_similarity_even_with_low_rerank():
    sel = select_evidence([ev("", "DOC1", rerank=-6, sim=0.78)], get_settings())
    assert sel.passed_gate and sel.evidence[0].label == "E1"


# ---------- R4 conflict resolution
def test_higher_authority_wins():
    gov, hosp = ev("E1", "GOV", authority=1), ev("E2", "HOSP", authority=3)
    assert resolve([hosp, gov]).winner is gov


def test_same_lineage_newer_version_wins():
    v1, v2 = ev("E1", "A1", version="1.0", lineage="L"), ev("E2", "A2", version="2.0", lineage="L")
    assert resolve([v1, v2]).winner is v2


def test_later_effective_date_wins():
    old, new = ev("E1", "A", eff=date(2026, 1, 1)), ev("E2", "B", eff=date(2026, 6, 1))
    assert resolve([old, new]).winner is new


def test_true_tie_is_unresolved():
    a, b = ev("E1", "A", authority=4, eff=date(2026, 10, 1)), ev("E2", "B", authority=4, eff=date(2026, 10, 1))
    assert resolve([a, b]).winner is None


# ---------- validator
EVIDENCE = [ev("E1", "DOC012", content="ICU visiting hours are 10:30 to 11:30 and 17:00 to 19:00. Max 2 visitors."),
            ev("E2", "DOC013", authority=1, content="Only 1 visitor per patient in ICU.")]


def draft(answer_type="retrieved_fact", answer="", cited=("E1",), conflicts=()):
    return LLMDraft(answer_type=answer_type, answer=answer, cited_evidence=list(cited),
                    conflicts=[LLMConflict(evidence_ids=list(c), topic="visitors") for c in conflicts])


def test_valid_answer_passes():
    r = validate_draft(draft(answer="Visiting is 10:30 to 11:30 and 17:00 to 19:00."), EVIDENCE, "ICU hours?")
    assert r.ok and r.final_answer_type == "retrieved_fact"


def test_invented_citation_rejected():
    r = validate_draft(draft(answer="Visiting is 10:30.", cited=("E9",)), EVIDENCE, "q")
    assert not r.ok and "does not exist" in r.errors[0]


def test_ungrounded_number_rejected():
    r = validate_draft(draft(answer="Visiting is 11:00 to 12:00."), EVIDENCE, "q")
    assert any("11:00" in e for e in r.errors)


def test_number_from_question_allowed():
    assert validate_draft(draft(answer="Yes, 7.5 is fine; the limit is 2 visitors."), EVIDENCE, "Is 7.5 ok?").ok


def test_resolved_conflict_must_cite_winner():
    bad = validate_draft(draft(answer="Max 2 visitors.", cited=("E1",), conflicts=[("E1", "E2")]), EVIDENCE, "q")
    assert not bad.ok and "E2" in bad.errors[0]
    good = validate_draft(draft(answer="Only 1 visitor (advisory overrides policy).", cited=("E1", "E2"),
                                conflicts=[("E1", "E2")]), EVIDENCE, "q")
    assert good.ok and good.conflicts[0].winner_document_id == "DOC013"


def test_unresolvable_conflict_becomes_conflict_flagged():
    tie = [ev("E1", "DOC001", authority=4, eff=date(2026, 10, 1), content="fast 4 hours"),
           ev("E2", "DOC004", authority=4, eff=date(2026, 10, 1), content="fast 6 hours")]
    r = validate_draft(draft(answer="One document says 4 hours, another 6 hours.", cited=("E1",),
                             conflicts=[("E1", "E2")]), tie, "q")
    assert r.ok and r.final_answer_type == "conflict_flagged"
    assert {e.document_id for e in r.cited} == {"DOC001", "DOC004"}  # both sides cited automatically


def test_numbers_normalised():
    assert numbers_in("INR 3,50,000 and 8.0 %") == {"350000", "8.0", "8"}
