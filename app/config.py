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
    database_url: str
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


@lru_cache
def get_settings() -> Settings:
    url = os.getenv("DATABASE_URL") or (
        f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
        f"@localhost:{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB')}"
    )
    return Settings(database_url=url)
