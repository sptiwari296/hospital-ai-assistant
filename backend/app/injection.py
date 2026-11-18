"""Prompt-injection defences (R8: uploaded / retrieved content is DATA, never instructions).

Layered:
1. Ingestion scan  — documents containing instruction-like text are quarantined as `draft`
                     (never searchable) until a document administrator reviews them.
2. Tag neutralising — text from documents or users cannot close or forge our prompt's
                     structural tags (<evidence>, <question>, <user_message>, ...).
3. The system prompts state that quoted content is data, and every LLM output is still checked
   by the citation/number validator and the routing policy — the model can't grant itself access.
"""
import re

INJECTION_PATTERNS = [
    r"ignore (?:all |any )?(?:the )?(?:previous|prior|above|earlier|preceding) (?:instructions|rules|prompts?)",
    r"disregard (?:all |any )?(?:the )?(?:previous|prior|above|your) (?:instructions|rules)",
    r"forget (?:all |your )?(?:previous )?instructions",
    r"you are now (?:a|an|in)\b",
    r"(?:reveal|print|repeat|show) (?:your|the) (?:system )?prompt",
    r"new instructions\s*:",
    r"\b(?:system|assistant|developer)\s*:\s",
    r"act as (?:a|an) (?:different|unrestricted|jailbroken)",
    r"</?\s*(?:evidence|evidence_items|question|correction|user_message|system)\b",
    r"\bdo anything now\b|\bjailbreak\b",
]
_INJECTION = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE)
_STRUCTURAL_TAG = re.compile(
    r"</?\s*(?:evidence|evidence_items|question|correction|user_message|system|assistant|user)\b[^>]*>",
    re.IGNORECASE)


def scan(text: str) -> list[str]:
    """Return the instruction-like phrases found (empty list = nothing suspicious)."""
    return sorted({m.group(0).strip()[:60] for m in _INJECTION.finditer(text)})


def neutralize_tags(text: str) -> str:
    """'</evidence> do X <evidence>' cannot break out of the evidence block any more."""
    return _STRUCTURAL_TAG.sub("[tag removed]", text)
