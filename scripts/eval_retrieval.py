"""Retrieval evaluation (free: no LLM calls).

Measures, on eval/rag_eval.json:
  * retrieval hit rate — the expected document appears in the selected evidence
  * gate accuracy      — answerable questions pass the not_found gate, unanswerable ones don't
  * forbidden hits     — superseded / future documents must never appear
Run:  .venv\\Scripts\\python scripts\\eval_retrieval.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.rag.query import preprocess  # noqa: E402
from app.rag.reranker import rerank  # noqa: E402
from app.rag.retriever import hybrid_search  # noqa: E402
from app.rag.selection import select_evidence  # noqa: E402


def main() -> None:
    settings = get_settings()
    cases = json.loads((ROOT / "eval" / "rag_eval.json").read_text(encoding="utf-8"))
    hits = gate_ok = answerable = forbidden = 0
    print(f"{'id':<4} {'gate':<6} {'hit':<4} top evidence (rerank / similarity)")
    for case in cases:
        q = preprocess(case["question"])
        selection = select_evidence(rerank(q.normalized, hybrid_search(q)), settings)
        docs = {e.document_id for e in selection.evidence}
        expected = set(case.get("expected_docs", [])) | set(case.get("also_expected", []))
        gate_correct = selection.passed_gate == case["answerable"]
        gate_ok += gate_correct
        hit = ""
        if case["answerable"]:
            answerable += 1
            hit = "yes" if expected <= docs else "NO"
            hits += hit == "yes"
        bad = docs & set(case.get("forbidden_docs", []))
        forbidden += bool(bad)
        top = ", ".join(f"{e.chunk_id}({e.rerank_score:.1f}/{e.vector_similarity:.2f})" for e in selection.evidence[:3])
        print(f"{case['id']:<4} {'ok' if gate_correct else 'WRONG':<6} {hit:<4} {top or selection.reason}"
              + (f"  FORBIDDEN {bad}" if bad else ""))
    print(f"\nRetrieval hit rate : {hits}/{answerable} = {hits / answerable:.0%}")
    print(f"Gate accuracy      : {gate_ok}/{len(cases)} = {gate_ok / len(cases):.0%}")
    print(f"Forbidden doc hits : {forbidden}")


if __name__ == "__main__":
    main()
