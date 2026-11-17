"""Step 1 tests: bad files must be rejected, good files accepted."""
import zipfile

import fitz
import pytest

from app.config import get_settings
from app.ingestion.validation import ValidationError, validate_file

KB_FILES = get_settings().storage_dir


def test_real_pdf_and_docx_pass():
    assert len(validate_file(KB_FILES / "ICU_Visiting_Policy_v2.pdf", get_settings())) == 64  # sha256 hex
    validate_file(KB_FILES / "Emergency_Procedures.docx", get_settings())


@pytest.mark.parametrize("name, content, reason", [
    ("virus.exe", b"MZ\x90\x00", "unsupported file type"),
    ("empty.pdf", b"", "empty"),
    ("fake.pdf", b"this is not a pdf", "does not match"),
    ("binary.txt", b"hello\x00world", "binary"),
    ("latin1.txt", "café".encode("latin-1"), "UTF-8"),
])
def test_bad_files_rejected(tmp_path, name, content, reason):
    f = tmp_path / name
    f.write_bytes(content)
    with pytest.raises(ValidationError, match=reason):
        validate_file(f, get_settings())


def test_pdf_with_javascript_rejected(tmp_path):
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "hello")
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", "<</S/JavaScript/JS(app.alert(1))>>")
    path = tmp_path / "js.pdf"
    doc.save(path)
    with pytest.raises(ValidationError, match="active content"):
        validate_file(path, get_settings())


def test_encrypted_pdf_rejected(tmp_path):
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "secret")
    path = tmp_path / "locked.pdf"
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    with pytest.raises(ValidationError, match="password"):
        validate_file(path, get_settings())


def test_docx_with_macro_rejected(tmp_path):
    path = tmp_path / "macro.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", "<w:document/>")
        zf.writestr("word/vbaProject.bin", b"macro")
    with pytest.raises(ValidationError, match="macros"):
        validate_file(path, get_settings())


def test_file_too_large_rejected(tmp_path):
    path = tmp_path / "big.txt"
    path.write_bytes(b"a" * (get_settings().max_upload_bytes + 1))
    with pytest.raises(ValidationError, match="limit"):
        validate_file(path, get_settings())
