"""Steps 2–4 tests: extraction keeps structure, cleaning keeps meaning, chunking respects sections."""
from app.config import get_settings
from app.ingestion.chunker import chunk_document, count_tokens
from app.ingestion.clean import clean_blocks, normalize_text
from app.ingestion.extract import extract_blocks
from app.ingestion.models import Block

KB_FILES = get_settings().storage_dir


# ---------------- extraction
def test_pdf_headings_and_pages_detected():
    blocks = extract_blocks(KB_FILES / "ICU_Visiting_Policy_v2.pdf")
    headings = {b.text: b.level for b in blocks if b.level}
    assert headings["ICU Visiting Policy"] == 1
    assert headings["3. Visiting hours"] == 2
    assert headings["3.1 Times"] == 3
    assert all(b.page == 1 for b in blocks)


def test_docx_and_pdf_give_same_heading_levels():
    blocks = extract_blocks(KB_FILES / "Emergency_Procedures.docx")
    headings = {b.text: b.level for b in blocks if b.level}
    assert headings["2. Emergency codes"] == 2 and headings["1.1 What to do"] == 3


def test_txt_extraction(tmp_path):
    f = tmp_path / "policy.txt"
    f.write_text("# Parking Policy\n\n1. Hours\nThe car park opens\nat 06:00.\n\n1.1 Night parking\nAllowed for visitors.\n",
                 encoding="utf-8")
    blocks = extract_blocks(f)
    assert [(b.text, b.level) for b in blocks] == [
        ("Parking Policy", 1), ("1. Hours", 2), ("The car park opens at 06:00.", 0),
        ("1.1 Night parking", 3), ("Allowed for visitors.", 0)]


# ---------------- cleaning
def test_ligatures_and_spaces_normalised():
    assert normalize_text("Eﬀective   date and pro­ﬁle") == "Effective date and profile"


def test_bullets_unified():
    assert normalize_text("- one\n▪ two") == "• one\n• two"


def test_warnings_and_conditions_are_kept():
    text = "Do NOT eat for 6 hours. Exception: diabetic patients must call the ward."
    assert clean_blocks([Block(text, 1)])[0].text == text


def test_page_numbers_and_repeated_footer_removed():
    blocks = []
    for page in range(1, 5):
        blocks += [Block(f"Real content {page}", page), Block("Hospital Confidential", page), Block(f"Page {page} of 4", page)]
    texts = [b.text for b in clean_blocks(blocks)]
    assert texts == ["Real content 1", "Real content 2", "Real content 3", "Real content 4"]


# ---------------- chunking
def _icu_chunks(**kw):
    return chunk_document(clean_blocks(extract_blocks(KB_FILES / "ICU_Visiting_Policy_v2.pdf")), "ICU Visiting Policy", **kw)


def test_section_kept_whole_with_heading():
    chunk = next(c for c in _icu_chunks() if c.section == "3")
    assert chunk.content.startswith("3. Visiting hours")
    assert "10:30 to 11:30" in chunk.content and "Children under 12" in chunk.content  # 3.1 and 3.3 together
    assert chunk.heading_path == "ICU Visiting Policy > 3. Visiting hours"
    assert chunk.page_start == 1


def test_short_preamble_skipped():
    assert all("Document ID" not in c.content for c in _icu_chunks())


def _long_section_blocks():
    sentences = [f"Rule {i}: visitors must follow instruction number {i} carefully." for i in range(40)]
    return [Block("Long Policy", 1, 1), Block("2. Rules", 1, 2), Block("2.1 General", 1, 3),
            Block(" ".join(sentences), 1, 0), Block("2.2 Children", 2, 3), Block("Children under 12 are not allowed.", 2, 0)]


def test_long_section_split_within_limit_and_with_overlap():
    chunks = chunk_document(_long_section_blocks(), "Long Policy", max_tokens=120, overlap_tokens=25)
    assert len(chunks) > 2
    assert all(c.token_count <= 120 for c in chunks)
    last_sentence_of_first = chunks[0].content.split(". ")[-1]
    assert last_sentence_of_first in chunks[1].content                     # overlap: repeated in next chunk
    assert chunks[1].content.splitlines()[:2] == ["2. Rules", "2.1 General"]  # continuation keeps both headings
    assert chunks[-1].section == "2.2" and chunks[-1].page_start == 2       # sub-section boundary respected


def test_token_counter():
    assert count_tokens("HbA1c must be below 8.0 %") == 8  # HbA1c | must | be | below | 8 | . | 0 | %
