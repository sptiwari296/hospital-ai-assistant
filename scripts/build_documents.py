"""Build the sample hospital knowledge base.

Reads the Markdown sources in data/knowledge_base/source/ and writes real
PDF / DOCX files to data/knowledge_base/files/, using the file names listed in
data/knowledge_base/metadata.json. Phase 2 (ingestion) reads those files.

Run:  .venv\\Scripts\\python scripts\\build_documents.py
"""
import json
import re
from pathlib import Path

import fitz  # PyMuPDF
import markdown
from docx import Document

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "data" / "knowledge_base"
SOURCE_DIR = KB / "source"
OUTPUT_DIR = KB / "files"

PDF_CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.35; }
h1 { font-size: 17pt; color: #1f4e79; }
h2 { font-size: 13pt; color: #1f4e79; margin-top: 10pt; }
h3 { font-size: 11pt; margin-top: 6pt; }
"""


LIST_LINE = re.compile(r"^(- |\d+\. )")


def ensure_blank_line_before_lists(md_text: str) -> str:
    """Python-Markdown only recognises a list if a blank line precedes it.
    Without this, '...surgery:\\n- CBC' is flattened into one paragraph."""
    out: list[str] = []
    for line in md_text.splitlines():
        if LIST_LINE.match(line) and out and out[-1].strip() and not LIST_LINE.match(out[-1]):
            out.append("")
        out.append(line)
    return "\n".join(out)


def build_pdf(md_text: str, out_path: Path, title: str) -> None:
    """Markdown -> HTML -> paginated A4 PDF."""
    html = markdown.markdown(ensure_blank_line_before_lists(md_text))
    story = fitz.Story(html=html, user_css=PDF_CSS)
    page_rect = fitz.paper_rect("a4")
    content_rect = page_rect + (54, 54, -54, -54)  # ~2 cm margins

    writer = fitz.DocumentWriter(str(out_path))
    more = True
    while more:
        device = writer.begin_page(page_rect)
        more, _ = story.place(content_rect)
        story.draw(device)
        writer.end_page()
    writer.close()

    # Add PDF metadata (title) so ingestion can read it too
    doc = fitz.open(out_path)
    doc.set_metadata({"title": title, "author": "Hospital Document Control (synthetic)"})
    doc.saveIncr()
    doc.close()


def add_runs(paragraph, text: str) -> None:
    """Add text to a DOCX paragraph, turning **bold** and *italic* into formatting."""
    for part in re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text):
        if part.startswith("**") and part.endswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 1:
            paragraph.add_run(part[1:-1]).italic = True
        elif part:
            paragraph.add_run(part)


def build_docx(md_text: str, out_path: Path, title: str) -> None:
    """Very small Markdown -> DOCX converter (headings, bullets, numbered lists, paragraphs)."""
    doc = Document()
    doc.core_properties.title = title
    for line in md_text.splitlines():
        line = line.rstrip()
        if not line:
            continue
        heading = re.match(r"^(#{1,3}) (.*)", line)
        if heading:
            doc.add_heading(heading.group(2), level=len(heading.group(1)))
        elif line.startswith("- "):
            add_runs(doc.add_paragraph(style="List Bullet"), line[2:])
        elif re.match(r"^\d+\. ", line):
            add_runs(doc.add_paragraph(style="List Number"), re.sub(r"^\d+\. ", "", line))
        else:
            add_runs(doc.add_paragraph(), line)
    doc.save(out_path)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    metadata = json.loads((KB / "metadata.json").read_text(encoding="utf-8"))

    for meta in metadata:
        doc_id, out_name = meta["document_id"], meta["document_name"]
        sources = list(SOURCE_DIR.glob(f"{doc_id}_*.md"))
        if len(sources) != 1:
            raise SystemExit(f"{doc_id}: expected 1 source file, found {len(sources)}")

        md_text = sources[0].read_text(encoding="utf-8")
        out_path = OUTPUT_DIR / out_name
        if out_name.endswith(".pdf"):
            build_pdf(md_text, out_path, meta["title"])
        elif out_name.endswith(".docx"):
            build_docx(md_text, out_path, meta["title"])
        else:
            raise SystemExit(f"{doc_id}: unsupported output type {out_name}")
        print(f"  built {out_name}")

    print(f"Done: {len(metadata)} documents in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
