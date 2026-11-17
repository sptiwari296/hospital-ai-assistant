"""Step 1 — Upload validation and security checks.

We never trust the file extension alone. Each check answers one question:
  type?      extension is on the allow-list AND the file's first bytes ("magic bytes") match it
  size?      not empty, not larger than the limit
  integrity? the file actually opens and is not corrupted
  security?  no encryption, JavaScript, embedded files, macros or zip bombs
"""
import hashlib
import zipfile
from pathlib import Path

import docx
import fitz  # PyMuPDF

from app.config import Settings

MAGIC_BYTES = {".pdf": b"%PDF-", ".docx": b"PK\x03\x04", ".txt": None}
PDF_DANGEROUS_KEYS = ("/JavaScript", "/JS", "/Launch", "/EmbeddedFile", "/RichMedia")


class ValidationError(Exception):
    """Raised when an uploaded file must be rejected."""


def validate_file(path: Path, settings: Settings) -> str:
    """Run all checks. Returns the file's SHA-256 checksum, or raises ValidationError."""
    path = Path(path)
    ext = path.suffix.lower()

    if not path.is_file():
        raise ValidationError("file not found")
    if ext not in MAGIC_BYTES:
        raise ValidationError(f"unsupported file type '{ext}' (allowed: .pdf, .docx, .txt)")

    size = path.stat().st_size
    if size == 0:
        raise ValidationError("file is empty")
    if size > settings.max_upload_bytes:
        raise ValidationError(f"file is {size:,} bytes; limit is {settings.max_upload_bytes:,}")

    data = path.read_bytes()
    magic = MAGIC_BYTES[ext]
    if magic and not data.startswith(magic):
        raise ValidationError(f"content does not match a real {ext} file (possible renamed file)")

    {".pdf": _check_pdf, ".docx": _check_docx, ".txt": _check_txt}[ext](path, data, settings)
    return hashlib.sha256(data).hexdigest()


def _check_pdf(path: Path, data: bytes, settings: Settings) -> None:
    try:
        doc = fitz.open(path)
    except Exception as exc:  # corrupted file
        raise ValidationError(f"PDF cannot be opened: {exc}") from exc
    with doc:
        if doc.needs_pass or doc.is_encrypted:
            raise ValidationError("PDF is password-protected/encrypted")
        if doc.page_count == 0:
            raise ValidationError("PDF has no pages")
        if doc.page_count > settings.max_pdf_pages:
            raise ValidationError(f"PDF has {doc.page_count} pages; limit is {settings.max_pdf_pages}")
        if doc.embfile_count() > 0:
            raise ValidationError("PDF contains embedded files")
        for xref in range(1, doc.xref_length()):
            obj = doc.xref_object(xref, compressed=False)
            for key in PDF_DANGEROUS_KEYS:
                if key in obj:
                    raise ValidationError(f"PDF contains active content ({key})")


def _check_docx(path: Path, data: bytes, settings: Settings) -> None:
    if not zipfile.is_zipfile(path):
        raise ValidationError("DOCX is not a valid zip container")
    with zipfile.ZipFile(path) as zf:
        if zf.testzip() is not None:
            raise ValidationError("DOCX is corrupted (CRC check failed)")
        names = zf.namelist()
        if "word/document.xml" not in names:
            raise ValidationError("not a Word document (word/document.xml missing)")
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            raise ValidationError("DOCX contains macros")
        if sum(i.file_size for i in zf.infolist()) > settings.max_docx_uncompressed_bytes:
            raise ValidationError("DOCX expands to an unsafe size (possible zip bomb)")
    try:
        docx.Document(path)
    except Exception as exc:
        raise ValidationError(f"DOCX cannot be opened: {exc}") from exc


def _check_txt(path: Path, data: bytes, settings: Settings) -> None:
    if b"\x00" in data:
        raise ValidationError("text file contains binary data")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("text file is not valid UTF-8") from exc
