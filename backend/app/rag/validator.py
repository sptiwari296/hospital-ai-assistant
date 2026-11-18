"""Step 8 — Citation and grounding validation (deterministic, no LLM).

Checks the LLM draft BEFORE anyone sees it:
  1. every cited label exists in the evidence we actually provided (no invented sources)
  2. factual answers cite at least one source; conflict answers cite two documents
  3. reported conflicts are resolved with R4 rules; the answer must follow the winner
  4. every number/time in the answer appears in the cited evidence or the question
     (catches "11:00" when the evidence says "10:30")
"""
import re
from dataclasses import dataclass, field

from app.rag.conflicts import resolve
from app.rag.models import ConflictReport, Evidence, LLMDraft

NUMBER = re.compile(r"\d[\d,]*(?:[.:]\d+)?")


def numbers_in(text: str) -> set[str]:
    out = set()
    for raw in NUMBER.findall(text):
        n = raw.replace(",", "")
        out.add(n)
        if "." in n and n.endswith("0"):  # "8.0" also accepted as "8"
            out.add(n.rstrip("0").rstrip("."))
    return out


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    conflicts: list[ConflictReport] = field(default_factory=list)
    final_answer_type: str = ""
    cited: list[Evidence] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_draft(draft: LLMDraft, evidence: list[Evidence], question: str) -> ValidationResult:
    by_label = {e.label: e for e in evidence}
    result = ValidationResult(final_answer_type=draft.answer_type)

    # 1. citations must point to evidence we provided
    unknown = [lbl for lbl in draft.cited_evidence if lbl not in by_label]
    unknown += [lbl for c in draft.conflicts for lbl in c.evidence_ids if lbl not in by_label]
    if unknown:
        result.errors.append(f"cited evidence that does not exist: {sorted(set(unknown))}")
    result.cited = [by_label[lbl] for lbl in dict.fromkeys(draft.cited_evidence) if lbl in by_label]

    if draft.answer_type == "not_found":
        return result
    if draft.answer_type == "clarification_needed":
        if not draft.answer.strip():
            result.errors.append("clarification_needed without a clarifying question")
        return result

    # 2. factual answers need sources
    if not result.cited:
        result.errors.append(f"{draft.answer_type} answer has no valid citation")

    # 3. conflicts: the LLM detects, the R4 rules decide
    #    (runs before the number check because unresolved conflicts add the other side's citation)
    unresolved = False
    for c in draft.conflicts:
        items = [by_label[lbl] for lbl in c.evidence_ids if lbl in by_label]
        if len({e.document_id for e in items}) < 2:
            continue  # same document — not a policy conflict
        res = resolve(items)
        if res.winner is None:
            unresolved = True
            result.conflicts.append(ConflictReport(evidence_ids=c.evidence_ids, topic=c.topic,
                                                   resolution="unresolved", rule=res.rule))
            for e in items:                       # an unresolved conflict must show ALL sides
                if e not in result.cited:
                    result.cited.append(e)
        else:
            result.conflicts.append(ConflictReport(evidence_ids=c.evidence_ids, topic=c.topic,
                                                   resolution="resolved",
                                                   winner_document_id=res.winner.document_id, rule=res.rule))
            if res.winner not in result.cited:
                result.errors.append(f"conflict on '{c.topic}' is resolved by rule ({res.rule}): "
                                     f"the answer must follow and cite {res.winner.label}")
            else:  # also cite the overridden source, so the user sees what was overridden
                result.cited += [e for e in items if e not in result.cited]

    # 4. numeric grounding
    allowed = numbers_in(question)
    for ev in result.cited:
        allowed |= numbers_in(ev.content) | numbers_in(ev.version) | numbers_in(str(ev.effective_date))
    ungrounded = sorted(numbers_in(draft.answer) - allowed)
    if ungrounded:
        result.errors.append(f"numbers not found in the cited evidence: {ungrounded}")

    if unresolved:
        result.final_answer_type = "conflict_flagged"
    elif draft.answer_type == "conflict_flagged":
        if not draft.conflicts:
            result.errors.append("conflict_flagged but no conflicting evidence listed")
        else:  # every reported conflict was resolvable by the rules
            result.errors.append("the reported conflicts are resolvable by the hierarchy rules; "
                                 "answer with retrieved_fact based on the winning source")
    return result
