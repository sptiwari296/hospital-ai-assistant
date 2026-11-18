"""Ask the knowledge assistant a question from the command line.

Run:  .venv\\Scripts\\python scripts\\ask.py "What are the ICU visiting hours?"
Needs ANTHROPIC_API_KEY in .env for the LLM step (not_found answers work without it).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.rag.generator import LLMError  # noqa: E402
from app.rag.service import RagService  # noqa: E402

if __name__ == "__main__":
    service = RagService()
    for question in sys.argv[1:] or ["What are the ICU visiting hours?"]:
        print(f"\nQ: {question}")
        try:
            print(service.answer(question).model_dump_json(indent=2, exclude={"evidence_considered"}))
        except LLMError as exc:
            print(f"LLM unavailable: {exc}")
