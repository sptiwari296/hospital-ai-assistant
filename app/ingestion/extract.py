"""Step 2 — Text and structure extraction.

Output: a flat list of Blocks in reading order. Each Block is either a heading
(level 1 = biggest) or a body paragraph (level 0), with its page number when
the format has pages (PDF only).
"""
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import docx
import fitz  # PyMuPDF

from app.ingestion.models import Block

BULLET_START = re.compile(r"^\s*([•▪◦●‣∙\-*]|\d+[.)])\s+")


def extract_blocks(path: Path) -> list[Block]:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return _extract_pdf(path)
    if ext == ".docx":
        return _extract_docx(path)
    if ext == ".txt":
        return _extract_txt(path)
    raise ValueError(f"unsupported type {ext}")


# ---------------------------------------------------------------- PDF
@dataclass
class _Line:
    page: int
    block_no: int
    text: str
    size: float
    bold: bool


def _join_lines(prev: str, nxt: str) -> str:
    """Re-join lines that the PDF layout wrapped."""
    if BULLET_START.match(nxt):
        return f"{prev}\n{nxt}"            # a new list item starts on its own line
    if prev.endswith("-") and not prev.endswith(" -"):
        return prev + nxt                  # "pre-" + "operative" -> "pre-operative"
    return f"{prev} {nxt}"                 # ordinary word wrap


def _extract_pdf(path: Path) -> list[Block]:
    lines: list[_Line] = []
    with fitz.open(path) as doc:
        for page in doc:
            for block_no, block in enumerate(page.get_text("dict")["blocks"]):
                if block.get("type") != 0:  # 0 = text, 1 = image
                    continue
                for line in block["lines"]:
                    spans = [s for s in line["spans"] if s["text"].strip()]
                    if not spans:
                        continue
                    lines.append(_Line(
                        page=page.number + 1,
                        block_no=block_no,
                        text="".join(s["text"] for s in line["spans"]).strip(),
                        size=round(max(s["size"] for s in spans), 1),
                        # flag 16 = bold. A heading line is bold from start to end.
                        bold=all((s["flags"] & 16) or "Bold" in s["font"] for s in spans),
                    ))
    if not lines:
        return []

    # Body text size = the size used by the most characters.
    size_weight = Counter()
    for ln in lines:
        size_weight[ln.size] += len(ln.text)
    body_size = size_weight.most_common(1)[0][0]

    # Headings = fully bold lines larger than body text. Biggest size -> level 1.
    heading_sizes = sorted({ln.size for ln in lines if ln.bold and ln.size > body_size + 0.2}, reverse=True)
    level_of = {size: i + 1 for i, size in enumerate(heading_sizes)}

    blocks: list[Block] = []
    last_key = None  # (page, block_no, level) of the block we are appending to
    for ln in lines:
        level = level_of.get(ln.size, 0) if ln.bold and len(ln.text) < 150 else 0
        key = (ln.page, ln.block_no, level)
        if blocks and key == last_key:
            joiner = " " if level else None
            blocks[-1].text = f"{blocks[-1].text} {ln.text}" if joiner else _join_lines(blocks[-1].text, ln.text)
        else:
            blocks.append(Block(text=ln.text, page=ln.page, level=level))
        last_key = key
    return blocks


# ---------------------------------------------------------------- DOCX
def _extract_docx(path: Path) -> list[Block]:
    document = docx.Document(path)
    blocks: list[Block] = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = para.style.name if para.style is not None else ""
        if style == "Title":
            blocks.append(Block(text, None, 1))
        elif style.startswith("Heading"):
            digits = re.findall(r"\d+", style)
            blocks.append(Block(text, None, int(digits[0]) if digits else 1))
        elif "List" in style:
            blocks.append(Block(f"• {text}", None, 0))
        else:
            blocks.append(Block(text, None, 0))
    # Tables are kept (not dropped) — appended as pipe-separated rows.
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                blocks.append(Block(" | ".join(cells), None, 0))
    return blocks


# ---------------------------------------------------------------- TXT
MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+[A-Z][^.]{0,80}$")  # "3.1 Times", no full stop


def _extract_txt(path: Path) -> list[Block]:
    blocks: list[Block] = []
    paragraph: list[str] = []

    def flush():
        if paragraph:
            text = paragraph[0]
            for nxt in paragraph[1:]:
                text = _join_lines(text, nxt)
            blocks.append(Block(text, None, 0))
            paragraph.clear()

    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        md = MD_HEADING.match(line)
        num = NUMBERED_HEADING.match(line)
        if not line:
            flush()
        elif md:
            flush()
            blocks.append(Block(md.group(2).strip(), None, len(md.group(1))))
        elif num:
            flush()
            depth = num.group(1).count(".") + 2  # "3" -> level 2, "3.1" -> level 3 (level 1 = title)
            blocks.append(Block(line, None, depth))
        else:
            paragraph.append(line)
    flush()
    return blocks
