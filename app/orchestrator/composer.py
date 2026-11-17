"""Combine answer parts into one structured, cited final response.

Tool answers use the tools' own deterministic summaries (the LLM never rewrites numbers).
Document references produced by tools ("DOC003 §4.1") are turned into full citations
from the documents table, so every citation is real metadata.
"""
import re

from app.db import connect
from app.orchestrator.workflows import Part

# When parts disagree, the final answer_type is the first one present in this order.
PRIORITY = ["refused", "action_completed", "conflict_flagged", "clarification_needed", "calculated",
            "retrieved_fact", "not_found"]
DOC_REF = re.compile(r"^(DOC\d{3,})(?: §([\d.]+))?$")


def merge_answer_type(parts: list[Part]) -> str:
    present = {p.answer_type for p in parts}
    return next((t for t in PRIORITY if t in present), "not_found")


def document_citations(references: list[str]) -> list[dict]:
    wanted = [(m.group(1), m.group(2)) for r in references if (m := DOC_REF.match(r))]
    if not wanted:
        return []
    with connect() as conn:
        docs = {d["document_id"]: d for d in conn.execute(
            """SELECT document_id, document_name, title, version, effective_date FROM documents
               WHERE document_id = ANY(%s)""", ([d for d, _ in wanted],)).fetchall()}
    out = []
    for doc_id, section in dict.fromkeys(wanted):
        d = docs.get(doc_id)
        if d:
            out.append({"document_id": doc_id, "document_name": d["document_name"], "title": d["title"],
                        "section": section, "version": d["version"], "effective_date": d["effective_date"].isoformat()})
    return out


def compose(parts: list[Part]) -> dict:
    citations: list[dict] = []
    for p in parts:
        citations += p.citations
        citations += document_citations(p.references)
        citations += [{"system": s} for s in p.sources]
    unique = list({tuple(sorted(c.items())): c for c in citations}.values())
    return {
        "answer": "\n\n".join(p.text for p in parts if p.text).strip(),
        "answer_type": merge_answer_type(parts),
        "citations": unique,
        "conflicts": [c for p in parts for c in p.conflicts],
        "escalation": next((p.escalation for p in parts if p.escalation), None),
        "confirmation": next((p.confirmation for p in parts if p.confirmation), None),
    }
