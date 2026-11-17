"""All settings in one place. Values come from .env / environment variables."""
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # harmless model-cache warning on Windows


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    database_url: str           # application role (least privilege, Row-Level Security applies)
    admin_database_url: str     # owner role: setup scripts, migrations, test fixtures only
    # --- embeddings ---
    embedding_model: str = "BAAI/bge-small-en-v1.5"  # local, free, 384 dims, max input 512 tokens
    embedding_dim: int = 384
    model_cache_dir: Path = ROOT / ".cache" / "fastembed"
    # --- chunking (starting values to evaluate in Phase 8, not "optimal") ---
    # bge-small reads at most 512 tokens, so 500–800-token chunks would be silently cut off.
    chunk_max_tokens: int = field(default_factory=lambda: _int("CHUNK_MAX_TOKENS", 400))
    chunk_overlap_tokens: int = field(default_factory=lambda: _int("CHUNK_OVERLAP_TOKENS", 80))
    min_preamble_tokens: int = 60  # text before the first section shorter than this is skipped
    # --- upload limits ---
    max_upload_bytes: int = 20 * 1024 * 1024  # 20 MB
    max_pdf_pages: int = 300
    max_docx_uncompressed_bytes: int = 100 * 1024 * 1024  # zip-bomb guard
    storage_dir: Path = ROOT / "data" / "knowledge_base" / "files"
    # --- RAG (Phase 3) ---
    retrieval_k: int = 20                     # candidates taken from EACH of vector and keyword search
    rrf_k: int = 60                           # reciprocal-rank-fusion constant (standard value)
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"  # local cross-encoder
    max_evidence: int = 6                     # chunks shown to the LLM
    # Relevance gate (calibrated on eval/rag_eval.json): a chunk counts as evidence if
    # its rerank score OR its vector similarity is high enough.
    min_rerank_score: float = field(default_factory=lambda: float(os.getenv("MIN_RERANK_SCORE", -4.0)))
    min_vector_similarity: float = field(default_factory=lambda: float(os.getenv("MIN_VECTOR_SIMILARITY", 0.72)))
    # --- LLM ---
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "groq"))  # "groq" | "claude"
    groq_model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-opus-5-5"))
    llm_effort: str = field(default_factory=lambda: os.getenv("LLM_EFFORT", "medium"))
    # Routing is an easy task: low reasoning effort keeps the classifier fast
    classifier_effort: str = field(default_factory=lambda: os.getenv("CLASSIFIER_EFFORT", "low"))


@lru_cache
def get_settings() -> Settings:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port, db = os.getenv("POSTGRES_PORT", "5432"), os.getenv("POSTGRES_DB")
    admin_url = os.getenv("ADMIN_DATABASE_URL") or (
        f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}@{host}:{port}/{db}")
    if not os.getenv("DATABASE_URL") and not os.getenv("API_DB_PASSWORD"):
        # fail closed: never silently fall back to the superuser, which would bypass Row-Level Security
        raise RuntimeError("API_DB_PASSWORD is not set. Run scripts/setup_security.py (see README).")
    app_url = os.getenv("DATABASE_URL") or (
        f"postgresql://{os.getenv('API_DB_USER', 'hospital_api')}:{os.getenv('API_DB_PASSWORD')}@{host}:{port}/{db}")
    return Settings(database_url=app_url, admin_database_url=admin_url)
