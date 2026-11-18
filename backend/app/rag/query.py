"""Step 1 — Query preprocessing.

Cleans the question and adds standard medical synonyms so KEYWORD search can match
documents that use the formal term (patients say "blood sugar", documents say "HbA1c").
The original wording is kept for the embedding and the LLM.
"""
import re
import unicodedata
from dataclasses import dataclass

MAX_QUESTION_CHARS = 1000

# patient wording -> wording used in hospital documents
SYNONYMS = {
    "icu": "intensive care",
    "tkr": "total knee replacement",
    "knee replacement": "joint replacement",
    "blood sugar": "glucose hba1c",
    "sugar": "glucose hba1c",
    "ecg": "electrocardiogram",
    "ekg": "ecg",
    "pre-op": "pre-operative",
    "preop": "pre-operative",
    "x-ray": "xray radiology",
    "fasting": "fast eat",
    "fast": "fasting eat",
    "covered": "coverage",
    "insurance": "coverage agreement",
    "discharge": "discharged",
    "visitors": "visiting visitor",
    "visiting": "visitors visitor",
}


@dataclass
class ProcessedQuery:
    original: str
    normalized: str
    keyword_text: str  # normalized question + synonym expansions


def preprocess(question: str) -> ProcessedQuery:
    text = unicodedata.normalize("NFKC", question)
    text = re.sub(r"\s+", " ", text).strip()[:MAX_QUESTION_CHARS]
    lower = text.lower()
    expansions = [syn for term, syn in SYNONYMS.items() if re.search(rf"\b{re.escape(term)}\b", lower)]
    return ProcessedQuery(original=question, normalized=text, keyword_text=" ".join([text, *expansions]))
