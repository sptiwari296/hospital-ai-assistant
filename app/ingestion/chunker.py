"""Step 4 — Section-aware chunking.

Rules (in priority order):
  1. A whole top-level section (e.g. "3. Visiting hours" with 3.1, 3.2, 3.3) is ONE chunk if it fits.
  2. If it is too long, cut only at sub-section boundaries (3.1 | 3.2), packing several together.
  3. Only if a single sub-section is still too long do we cut inside it — at sentence
     boundaries, with an overlap so a condition is never separated from its context.
Every chunk starts with its section heading, and remembers section number, title and pages.
"""
import re
from dataclasses import dataclass

from app.ingestion.models import Block, Chunk

TOKEN = re.compile(r"\w+|[^\w\s]")  # approximate tokens: words, numbers and punctuation marks
SECTION_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def count_tokens(text: str) -> int:
    return len(TOKEN.findall(text))


def split_heading(text: str) -> tuple[str | None, str]:
    """'3.1 Times' -> ('3.1', 'Times');  'Purpose' -> (None, 'Purpose')"""
    m = SECTION_NUMBER.match(text)
    return (m.group(1), m.group(2)) if m else (None, text)


@dataclass
class _Unit:
    """A paragraph, list line or sub-heading inside a section."""
    text: str
    page: int | None
    sub_heading: str | None  # e.g. "3.1 Times" — which sub-section this unit belongs to
    para_id: int = 0         # sentences of the same paragraph share an id


@dataclass
class _Section:
    heading: str             # "3. Visiting hours"
    units: list[_Unit]


# ------------------------------------------------------------------ grouping
def _group_into_sections(blocks: list[Block]) -> tuple[str | None, list[_Unit], list[_Section]]:
    """Returns (title found in the document, preamble units, sections)."""
    levels = sorted({b.level for b in blocks if b.level > 0})
    title, title_level = None, None
    if levels:
        top = [b for b in blocks if b.level == levels[0]]
        if len(top) == 1 and len(levels) > 1:  # a single biggest heading = the document title
            title, title_level = top[0].text, levels[0]
    section_levels = [lv for lv in levels if lv != title_level]
    section_level = section_levels[0] if section_levels else None

    preamble: list[_Unit] = []
    sections: list[_Section] = []
    sub_heading = None
    for i, b in enumerate(blocks):
        if b.level and b.level == title_level:
            continue
        if b.level and b.level == section_level:
            sections.append(_Section(b.text, []))
            sub_heading = None
            continue
        if b.level:  # deeper heading = sub-section
            sub_heading = b.text
        target = sections[-1].units if sections else preamble
        target.append(_Unit(b.text, b.page, sub_heading, para_id=i))
    return title, preamble, sections


def _split_by_subsection(units: list[_Unit]) -> list[list[_Unit]]:
    groups: list[list[_Unit]] = []
    for u in units:
        if not groups or u.sub_heading != groups[-1][0].sub_heading:
            groups.append([])
        groups[-1].append(u)
    return groups


def _to_sentences(units: list[_Unit], max_tokens: int) -> list[_Unit]:
    """Explode paragraphs into sentences / list lines so we can cut between them."""
    out: list[_Unit] = []
    for u in units:
        for line in u.text.split("\n"):
            for sentence in SENTENCE_END.split(line):
                if count_tokens(sentence) <= max_tokens:
                    out.append(_Unit(sentence, u.page, u.sub_heading, u.para_id))
                else:  # pathological very long sentence: hard cut by words
                    words = sentence.split()
                    step = max_tokens // 2
                    for start in range(0, len(words), step):
                        out.append(_Unit(" ".join(words[start:start + step]), u.page, u.sub_heading, u.para_id))
    return out


def _pack_with_overlap(units: list[_Unit], budget: int, overlap: int) -> list[list[_Unit]]:
    pieces: list[list[_Unit]] = []
    current: list[_Unit] = []
    used = 0
    for u in units:
        t = count_tokens(u.text)
        if current and used + t > budget:
            pieces.append(current)
            carry, carried = [], 0          # repeat the last sentences in the next chunk
            for prev in reversed(current):
                pt = count_tokens(prev.text)
                if carried + pt > overlap:
                    break
                carry.insert(0, prev)
                carried += pt
            current, used = carry, carried
        current.append(u)
        used += t
    if current:
        pieces.append(current)
    return pieces


# ------------------------------------------------------------------ main entry
def chunk_document(blocks: list[Block], doc_title: str, max_tokens: int = 400,
                   overlap_tokens: int = 80, min_preamble_tokens: int = 60) -> list[Chunk]:
    found_title, preamble, sections = _group_into_sections(blocks)
    title = doc_title or found_title or "Untitled document"

    if not sections:  # document without any section headings: chunk it as one big section
        sections, preamble = [_Section("", preamble)], []
    elif preamble and count_tokens(" ".join(u.text for u in preamble)) >= min_preamble_tokens:
        sections.insert(0, _Section("Introduction", preamble))
    # (a short preamble is only the title/header lines — already captured in metadata)

    chunks: list[Chunk] = []

    def emit(section: _Section, units: list[_Unit]) -> None:
        subs = {u.sub_heading for u in units}
        sub = subs.pop() if len(subs) == 1 else None      # chunk lies inside ONE sub-section
        number, name = split_heading(sub or section.heading)
        body, last_para = "", None
        for u in units:
            same_paragraph = u.para_id == last_para and not u.text.startswith("• ") and u.text != u.sub_heading
            body += (" " if same_paragraph else "\n") + u.text if body else u.text
            last_para = u.para_id
        # Continuation pieces repeat their sub-heading so they never lose context
        if sub and units[0].text != sub:
            body = f"{sub}\n{body}"
        content = f"{section.heading}\n{body}".strip() if section.heading else body
        path = " > ".join(p for p in (title, section.heading, sub) if p)
        pages = [u.page for u in units if u.page is not None]
        chunks.append(Chunk(
            chunk_index=len(chunks), section=number, section_title=name or None, heading_path=path,
            page_start=min(pages) if pages else None, page_end=max(pages) if pages else None,
            content=content, token_count=count_tokens(content), doc_title=title,
        ))

    for section in sections:
        # headings are repeated in every chunk, so they come out of the budget
        longest_sub = max((count_tokens(u.sub_heading) for u in section.units if u.sub_heading), default=0)
        budget = max_tokens - count_tokens(section.heading) - longest_sub
        if count_tokens(" ".join(u.text for u in section.units)) <= budget:
            emit(section, section.units)                                    # rule 1
            continue
        packed: list[_Unit] = []
        for group in _split_by_subsection(section.units):                  # rule 2
            g_tokens = count_tokens(" ".join(u.text for u in group))
            if g_tokens > budget:                                           # rule 3
                if packed:
                    emit(section, packed)
                    packed = []
                for piece in _pack_with_overlap(_to_sentences(group, budget), budget, overlap_tokens):
                    emit(section, piece)
                continue
            if packed and count_tokens(" ".join(u.text for u in packed)) + g_tokens > budget:
                emit(section, packed)
                packed = []
            packed.extend(group)
        if packed:
            emit(section, packed)
    return chunks
