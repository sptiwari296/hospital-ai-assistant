"""LLM providers (used by the RAG generator and the intent classifier).

`LLMProvider` is the provider pattern: callers depend on this interface, so tests use
fake providers and the model can be swapped without touching the pipeline.
Two real providers: GroqProvider (default, gpt-oss-120b) and ClaudeProvider.
Choose with LLM_PROVIDER=groq|claude in .env.

Every call uses STRUCTURED OUTPUT (a strict JSON schema): the reply is guaranteed to be
valid JSON with exactly the expected fields, so we never parse free text.
"""
import json
import os
from abc import ABC
from functools import lru_cache

from app.config import Settings, get_settings
from app.rag.models import LLMDraft

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "answer_type": {"type": "string",
                        "enum": ["retrieved_fact", "not_found", "conflict_flagged", "clarification_needed"]},
        "answer": {"type": "string"},
        "cited_evidence": {"type": "array", "items": {"type": "string"}},
        "conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"evidence_ids": {"type": "array", "items": {"type": "string"}},
                               "topic": {"type": "string"}},
                "required": ["evidence_ids", "topic"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["answer_type", "answer", "cited_evidence", "conflicts"],
    "additionalProperties": False,
}


class LLMError(Exception):
    """The model could not produce a usable answer (no credentials, network, refusal...)."""


class LLMProvider(ABC):
    def complete_json(self, system: str, user_message: str, schema: dict, name: str,
                      effort: str | None = None) -> tuple[dict, str]:
        """Return (JSON object matching `schema`, name of the model that answered).
        `effort` overrides the default reasoning effort (e.g. "low" for quick routing)."""
        raise NotImplementedError

    def generate(self, system: str, user_message: str) -> tuple[LLMDraft, str]:
        """RAG answer draft."""
        data, model = self.complete_json(system, user_message, DRAFT_SCHEMA, "answer_draft")
        try:
            return LLMDraft.model_validate(data), model
        except ValueError as exc:
            raise LLMError(f"model output did not match the schema: {exc}") from exc


class ClaudeProvider(LLMProvider):
    def __init__(self, settings: Settings):
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic()  # credentials from ANTHROPIC_API_KEY (.env) or `ant auth login`
        self._settings = settings

    def complete_json(self, system: str, user_message: str, schema: dict, name: str,
                      effort: str | None = None) -> tuple[dict, str]:
        a = self._anthropic
        try:
            response = self._client.beta.messages.create(
                model=self._settings.llm_model,
                max_tokens=16000,
                # Stable system prompt is cached: later questions pay ~10% for these tokens.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_message}],
                output_config={"effort": effort or self._settings.llm_effort,
                               "format": {"type": "json_schema", "schema": schema}},
                # If a safety classifier declines, the API retries on a fallback model automatically.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.AuthenticationError as exc:
            raise LLMError("Anthropic API key missing or invalid (set ANTHROPIC_API_KEY in .env)") from exc
        except a.RateLimitError as exc:
            raise LLMError("LLM rate limit reached; try again shortly") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"LLM API error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise LLMError("cannot reach the LLM API (network)") from exc
        except TypeError as exc:  # raised by the SDK when no credentials can be resolved at all
            raise LLMError(f"Anthropic client not configured: {exc}") from exc

        if response.stop_reason == "refusal":
            raise LLMError("the model declined to answer this request")
        if response.stop_reason == "max_tokens":
            raise LLMError("the model's answer was cut off (max_tokens)")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMError("the model returned no text")
        try:
            return json.loads(text), response.model
        except json.JSONDecodeError as exc:
            raise LLMError(f"model output was not valid JSON: {exc}") from exc


class GroqProvider(LLMProvider):
    """gpt-oss-120b on Groq via LangChain, with strict JSON-schema structured output."""

    def __init__(self, settings: Settings):
        if not os.getenv("GROQ_API_KEY"):
            raise LLMError("GROQ_API_KEY missing (add it to .env)")
        from langchain_groq import ChatGroq

        self.model = settings.groq_model
        self._settings = settings
        self._chat_groq = ChatGroq
        self._chains: dict[tuple[str, str], object] = {}

    def complete_json(self, system: str, user_message: str, schema: dict, name: str,
                      effort: str | None = None) -> tuple[dict, str]:
        effort = effort or self._settings.llm_effort
        key = (name, effort)
        if key not in self._chains:  # one chain per (schema, effort); LangChain needs a title on a raw schema
            llm = self._chat_groq(model=self.model, temperature=0, max_retries=2, reasoning_effort=effort)
            titled = {"title": name, "description": f"Structured output: {name}", **schema}
            self._chains[key] = llm.with_structured_output(titled, method="json_schema", strict=True,
                                                           include_raw=True)
        try:
            out = self._chains[key].invoke([("system", system), ("human", user_message)])
        except Exception as exc:  # network, auth, rate limit, invalid request
            raise LLMError(f"Groq API error: {type(exc).__name__}: {exc}") from exc
        if out.get("parsing_error") or out.get("parsed") is None:
            raise LLMError(f"model output did not match the schema: {out.get('parsing_error')}")
        return out["parsed"], out["raw"].response_metadata.get("model_name", self.model)


@lru_cache
def get_llm_provider() -> LLMProvider:
    """Pick the LLM from LLM_PROVIDER in .env — the rest of the pipeline doesn't care which."""
    settings = get_settings()
    try:
        if settings.llm_provider == "groq":
            return GroqProvider(settings)
        if settings.llm_provider == "claude":
            return ClaudeProvider(settings)
    except LLMError:
        raise
    except Exception as exc:  # e.g. no credentials found when constructing a client
        raise LLMError(f"{settings.llm_provider} client not configured: {exc}") from exc
    raise LLMError(f"unknown LLM_PROVIDER '{settings.llm_provider}' (use 'groq' or 'claude')")
