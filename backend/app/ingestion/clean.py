"""Step 3 — Cleaning and normalisation.

Removes noise (odd characters, extra spaces, page numbers, repeated headers/footers).
NEVER removes content words — clinical conditions, exceptions and warnings stay.
"""
import re
import unicodedata
from collections import defaultdict

from app.ingestion.models import Block

INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)  # zero-width chars, soft hyphen
BULLET = re.compile(r"^[•▪◦●‣∙\-*]\s+")
PAGE_NUMBER = re.compile(r"^(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?$", re.IGNORECASE)


def normalize_text(text: str) -> str:
    # NFKC turns look-alike characters into plain ones: "ﬀ" -> "ff", "ﬁ" -> "fi", non-breaking space -> space
    text = unicodedata.normalize("NFKC", text).translate(INVISIBLE)
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        line = BULLET.sub("• ", line)  # one bullet style everywhere
        if line:
            lines.append(line)
    return "\n".join(lines)


def clean_blocks(blocks: list[Block]) -> list[Block]:
    cleaned = [Block(normalize_text(b.text), b.page, b.level) for b in blocks]
    cleaned = [b for b in cleaned if b.text and not PAGE_NUMBER.match(b.text)]

    # Repeated headers/footers: the same short body line on at least half of the pages (3+ pages).
    pages = {b.page for b in cleaned if b.page is not None}
    if len(pages) >= 3:
        pages_with_text = defaultdict(set)
        for b in cleaned:
            if b.level == 0 and b.page is not None and len(b.text) < 200:
                pages_with_text[b.text].add(b.page)
        repeated = {t for t, ps in pages_with_text.items() if len(ps) >= max(3, len(pages) / 2)}
        cleaned = [b for b in cleaned if b.text not in repeated]

    # Drop exact duplicates that follow each other (e.g. text extracted twice)
    result: list[Block] = []
    for b in cleaned:
        if not result or (b.text, b.level) != (result[-1].text, result[-1].level):
            result.append(b)
    return result
