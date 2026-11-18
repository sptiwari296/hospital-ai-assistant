"""Data shapes for the RAG (knowledge pathway)."""
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

NOT_FOUND_MESSAGE = "I could not find this information in the authorised hospital sources."

AUTHORITY_NAMES = {1: "government / regulatory", 2: "clinical guideline", 3: "hospital-wide policy", 4: "department SOP"}


@dataclass
class Evidence:
    """One retrieved chunk plus its document metadata and scores."""
    chunk_id: str
    document_id: str
    document_name: str
    title: str
    department: str
    document_type: str
    lineage_id: str
    version: str
    effective_date: date
    authority_level: int
    section: str | None
    section_title: str | None
    heading_path: str
    page_start: int | None
    content: str
    vector_similarity: float = 0.0
    keyword_rank: int | None = None  # position in keyword results (1 = best), None = not matched
    rrf_score: float = 0.0
    rerank_score: float | None = None
    label: str = ""                  # "E1", "E2"... — the ONLY handle the LLM gets for citing


@dataclass
class RetrievalFilters:
    """Optional metadata filters (e.g. a staff user scoped to a department)."""
    departments: list[str] | None = None
    document_types: list[str] | None = None


class Citation(BaseModel):
    """Built from database metadata — never from LLM text (so citations can't be invented)."""
    document_id: str
    document_name: str
    title: str
    section: str | None
    section_title: str | None
    version: str
    effective_date: date
    page: int | None


class ConflictReport(BaseModel):
    evidence_ids: list[str]
    topic: str
    resolution: Literal["resolved", "unresolved"]
    winner_document_id: str | None = None
    rule: str  # which R4 rule decided it


# ---- What the LLM must return (structured output). It can only REFER to evidence by label.
class LLMConflict(BaseModel):
    evidence_ids: list[str] = Field(description="Labels of evidence items that disagree, e.g. ['E1','E3']")
    topic: str = Field(description="What they disagree about, in a few words")


class LLMDraft(BaseModel):
    answer_type: Literal["retrieved_fact", "not_found", "conflict_flagged", "clarification_needed"]
    answer: str
    cited_evidence: list[str] = Field(description="Labels of the evidence items that support the answer")
    conflicts: list[LLMConflict]


class RagAnswer(BaseModel):
    """Final answer returned by the knowledge pathway."""
    trace_id: str
    answer: str
    answer_type: Literal["retrieved_fact", "not_found", "conflict_flagged", "clarification_needed"]
    citations: list[Citation] = []
    conflicts: list[ConflictReport] = []
    explanation: str = ""
    evidence_considered: list[dict] = []    # for audit/debugging: chunk ids + scores
    validation_errors: list[str] = []
    model: str | None = None
    latency_ms: int = 0
