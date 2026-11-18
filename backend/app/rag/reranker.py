"""Step 5a — Reranking.

The retriever compares a question vector with chunk vectors that were computed
separately (a "bi-encoder": fast, but approximate). A cross-encoder reads the
question and the chunk TOGETHER and scores how well the chunk answers it:
slower, but much more precise — so we only run it on the ~20–40 candidates.
"""
import re
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


MIN_SEGMENT_WORDS = 4


def _segments(ev: Evidence) -> list[str]:
    """Each line / sentence of a chunk, prefixed with its headings for context.
    List-style chunks (several unrelated rules) dilute a whole-chunk score; one line may answer the question."""
    out = []
    for line in ev.content.split("\n")[1:]:              # first line is the section heading itself
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            if len(sentence.split()) >= MIN_SEGMENT_WORDS:
                out.append(f"{ev.heading_path}: {sentence.lstrip('• ')}")
    return out


def rerank(question: str, candidates: list[Evidence], reranker: Reranker | None = None) -> list[Evidence]:
    """Max-sentence reranking: a chunk's score is the best of its whole-text score and its
    individual sentence scores (one batch call to the cross-encoder)."""
    reranker = reranker or get_reranker()
    passages, owner = [], []
    for i, c in enumerate(candidates):
        for p in [c.content, *_segments(c)]:
            passages.append(p)
            owner.append(i)
    best = [float("-inf")] * len(candidates)
    for i, score in zip(owner, reranker.score(question, passages), strict=True):
        best[i] = max(best[i], score)
    for ev, score in zip(candidates, best, strict=True):
        ev.rerank_score = score
    return sorted(candidates, key=lambda e: e.rerank_score, reverse=True)
