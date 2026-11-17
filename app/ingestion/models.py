"""Data shapes used by the ingestion pipeline."""
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

DocumentType = Literal[
    "hospital_sop", "clinical_protocol", "nursing_procedure", "admission_policy",
    "discharge_guideline", "insurance_agreement", "emergency_procedure", "government_advisory",
]


class DocumentMetadata(BaseModel):
    """The 'ID card' an administrator supplies with every upload. Pydantic rejects bad values."""
    document_id: str = Field(pattern=r"^DOC\d{3,}$")
    document_name: str = Field(pattern=r"^[\w\-. ]+\.(pdf|docx|txt)$")  # no folders, no odd characters
    title: str = Field(min_length=3)
    department: str
    document_type: DocumentType
    lineage_id: str = Field(pattern=r"^[A-Z0-9_]+$")  # same for every version of one policy
    version: str = Field(pattern=r"^\d+(\.\d+)*$")
    effective_date: date
    expiry_date: date | None = None
    approval_status: Literal["draft", "approved", "superseded", "retired"]
    authority_level: int = Field(ge=1, le=4)  # 1 = government ... 4 = department SOP
    uploaded_by: str

    @model_validator(mode="after")
    def expiry_after_effective(self):
        if self.expiry_date and self.expiry_date < self.effective_date:
            raise ValueError("expiry_date is before effective_date")
        return self


@dataclass
class Block:
    """One piece of extracted text: a heading (level >= 1) or a body paragraph (level 0)."""
    text: str
    page: int | None  # None for DOCX/TXT, which have no fixed pages
    level: int = 0


@dataclass
class Chunk:
    chunk_index: int
    section: str | None        # "3.1"
    section_title: str | None  # "Times"
    heading_path: str          # "ICU Visiting Policy > 3. Visiting hours > 3.1 Times"
    page_start: int | None
    page_end: int | None
    content: str
    token_count: int
    doc_title: str = ""

    @property
    def embed_text(self) -> str:
        """What we feed the embedding model: the document title gives every chunk context."""
        return f"{self.doc_title}\n{self.content}"


class IngestionReport(BaseModel):
    trace_id: str
    document_id: str
    status: Literal["ingested", "unchanged", "duplicate", "rejected"]
    chunk_count: int = 0
    superseded: list[str] = []
    message: str = ""
    latency_ms: int = 0
