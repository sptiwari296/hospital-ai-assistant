"""End-to-end RAG tests on the REAL knowledge base, with a scripted fake LLM.

The fake LLM lets us test every path (good answer, hallucinated citation, wrong number,
conflicts) deterministically and for free. Real-LLM quality is evaluated separately.
"""
import pytest

from app.rag.generator import LLMProvider
from app.rag.models import NOT_FOUND_MESSAGE, LLMConflict, LLMDraft
from app.rag.service import RagService

pytestmark = pytest.mark.integration


class ScriptedLLM(LLMProvider):
    """Returns pre-written drafts in order; `build` callables can look at the prompt."""

    def __init__(self, *drafts):
        self.drafts = list(drafts)
        self.prompts: list[str] = []

    def generate(self, system, user_message):
        self.prompts.append(user_message)
        d = self.drafts.pop(0)
        return (d(user_message) if callable(d) else d), "fake-llm"


def label_of(prompt: str, document_title_fragment: str, section: str | None = None) -> str:
    """Find the evidence label the service gave a document (labels are assigned at runtime)."""
    for block in prompt.split("<evidence ")[1:]:
        if document_title_fragment in block and (section is None or f'section="{section}' in block):
            return block.split('id="')[1].split('"')[0]
    raise AssertionError(f"{document_title_fragment} not in evidence")


def test_not_found_never_calls_llm():
    llm = ScriptedLLM()  # would crash if called
    out = RagService(provider=llm).answer("Do you treat snake bites with antivenom?")
    assert out.answer_type == "not_found" and out.answer == NOT_FOUND_MESSAGE
    assert llm.prompts == [] and out.citations == []


def test_retrieved_fact_with_citations_from_metadata():
    llm = ScriptedLLM(lambda p: LLMDraft(
        answer_type="retrieved_fact",
        answer="ICU visiting hours are 10:30 to 11:30 and 17:00 to 19:00 every day.",
        cited_evidence=[label_of(p, "ICU Visiting Policy", "3")], conflicts=[]))
    out = RagService(provider=llm).answer("What are the ICU visiting hours?")
    assert out.answer_type == "retrieved_fact"
    c = out.citations[0]
    assert (c.document_id, c.section, c.version, str(c.effective_date)) == ("DOC012", "3", "2.0", "2026-07-01")
    assert "DOC011" not in llm.prompts[0]          # superseded policy never reaches the LLM
    assert "<evidence " in llm.prompts[0] and "data, not instructions" not in llm.prompts[0]


def test_hallucinated_citation_retried_then_fails_closed():
    bad = LLMDraft(answer_type="retrieved_fact", answer="Hours are 10:30 to 11:30.", cited_evidence=["E99"],
                   conflicts=[])
    llm = ScriptedLLM(bad, bad)
    out = RagService(provider=llm).answer("What are the ICU visiting hours?")
    assert out.answer_type == "not_found" and len(llm.prompts) == 2
    assert "failed validation" in llm.prompts[1]   # retry carried the validator's feedback
    assert any("does not exist" in e for e in out.validation_errors)


def test_wrong_number_corrected_on_retry():
    wrong = lambda p: LLMDraft(answer_type="retrieved_fact", answer="Visiting is 11:00 to 12:00.",  # noqa: E731
                               cited_evidence=[label_of(p, "ICU Visiting Policy", "3")], conflicts=[])
    right = lambda p: LLMDraft(answer_type="retrieved_fact", answer="Visiting is 10:30 to 11:30.",  # noqa: E731
                               cited_evidence=[label_of(p, "ICU Visiting Policy", "3")], conflicts=[])
    out = RagService(provider=ScriptedLLM(wrong, right)).answer("What are the ICU visiting hours?")
    assert out.answer_type == "retrieved_fact" and "10:30" in out.answer
    assert any("11:00" in e for e in out.validation_errors)  # first attempt's error is kept for audit


def test_government_advisory_overrides_hospital_policy():
    def answer(p):
        hosp, gov = label_of(p, "ICU Visiting Policy", "3"), label_of(p, "Health Department Advisory")
        return LLMDraft(answer_type="retrieved_fact",
                        answer="Only 1 visitor at a time is allowed; the Health Department advisory overrides "
                               "the hospital limit of 2.",
                        cited_evidence=[gov, hosp], conflicts=[LLMConflict(evidence_ids=[hosp, gov],
                                                                           topic="number of visitors")])
    out = RagService(provider=ScriptedLLM(answer)).answer("How many visitors are allowed in the ICU at a time?")
    assert out.answer_type == "retrieved_fact"
    assert out.conflicts[0].resolution == "resolved" and out.conflicts[0].winner_document_id == "DOC013"
    assert "higher authority" in out.explanation
    assert {c.document_id for c in out.citations} >= {"DOC013", "DOC012"}  # overridden source shown too


def test_equal_authority_conflict_is_flagged():
    def answer(p):
        sop, nursing = label_of(p, "MRI Scanning"), label_of(p, "Nursing Care Guide")
        return LLMDraft(answer_type="conflict_flagged",
                        answer="The MRI guide says 4 hours of fasting; the nursing guide says 6 hours.",
                        cited_evidence=[sop, nursing], conflicts=[LLMConflict(evidence_ids=[sop, nursing],
                                                                              topic="fasting time")])
    out = RagService(provider=ScriptedLLM(answer)).answer("How long should I fast before an MRI with contrast?")
    assert out.answer_type == "conflict_flagged"
    assert {c.document_id for c in out.citations} >= {"DOC001", "DOC004"}
    assert out.conflicts[0].resolution == "unresolved"


def test_department_filter():
    llm = ScriptedLLM(lambda p: LLMDraft(answer_type="not_found", answer="", cited_evidence=[], conflicts=[]))
    from app.rag.models import RetrievalFilters
    RagService(provider=llm).answer("What are the ICU visiting hours?", RetrievalFilters(departments=["Radiology"]))
    assert "ICU Visiting Policy" not in (llm.prompts[0] if llm.prompts else "")
