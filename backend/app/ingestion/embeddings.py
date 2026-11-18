"""Step 6 — Embeddings (text -> vector of numbers).

`Embedder` is an abstract base class (the provider pattern), so we can swap the
local model for an API model later without touching the rest of the pipeline.
"""
from abc import ABC, abstractmethod
from functools import lru_cache

import numpy as np

from app.config import get_settings


class Embedder(ABC):
    name: str
    dim: int

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[np.ndarray]:
        """Vectors for chunks being stored."""

    @abstractmethod
    def embed_query(self, text: str) -> np.ndarray:
        """Vector for a user question (some models treat questions slightly differently)."""


class FastEmbedEmbedder(Embedder):
    """Runs BAAI/bge-small-en-v1.5 locally with ONNX: free, offline, no API key."""

    def __init__(self, model_name: str, dim: int, cache_dir: str):
        from fastembed import TextEmbedding  # imported here so tests not needing it stay fast

        self.name, self.dim = model_name, dim
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    def embed_documents(self, texts: list[str]) -> list[np.ndarray]:
        return list(self._model.passage_embed(texts))

    def embed_query(self, text: str) -> np.ndarray:
        # bge models add an instruction prefix to queries; fastembed does this for us
        return next(iter(self._model.query_embed(text)))


@lru_cache
def get_embedder() -> Embedder:
    """Load the model once per process (loading takes a few seconds)."""
    s = get_settings()
    return FastEmbedEmbedder(s.embedding_model, s.embedding_dim, str(s.model_cache_dir))
