"""Step 5a — Reranking.

The retriever compares a question vector with chunk vectors that were computed
separately (a "bi-encoder": fast, but approximate). A cross-encoder reads the
question and the chunk TOGETHER and scores how well the chunk answers it:
slower, but much more precise — so we only run it on the ~20–40 candidates.
"""
from abc import ABC, abstractmethod
from functools import lru_cache

from app.config import get_settings
from app.rag.models import Evidence


class Reranker(ABC):
    @abstractmethod
    def score(self, question: str, passages: list[str]) -> list[float]:
        """Higher = more relevant. One score per passage, same order."""


class CrossEncoderReranker(Reranker):
    def __init__(self, model_name: str, cache_dir: str):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model = TextCrossEncoder(model_name=model_name, cache_dir=cache_dir)

    def score(self, question: str, passages: list[str]) -> list[float]:
        return [float(s) for s in self._model.rerank(question, passages)] if passages else []


@lru_cache
def get_reranker() -> Reranker:
    s = get_settings()
    return CrossEncoderReranker(s.rerank_model, str(s.model_cache_dir))


def rerank(question: str, candidates: list[Evidence], reranker: Reranker | None = None) -> list[Evidence]:
    reranker = reranker or get_reranker()
    for ev, score in zip(candidates, reranker.score(question, [c.content for c in candidates]), strict=True):
        ev.rerank_score = score
    return sorted(candidates, key=lambda e: e.rerank_score, reverse=True)
