"""PDF and DOCX document parsing with input validation."""

from __future__ import annotations

import io
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Max file size: 10 MB
MAX_FILE_SIZE = 10 * 1024 * 1024

# Magic bytes for file type validation
PDF_MAGIC = b"%PDF"
DOCX_MAGIC = b"PK\x03\x04"  # ZIP/DOCX

ALLOWED_TYPES = {"pdf", "docx"}


class DocumentParseError(Exception):
    pass


def validate_file(data: bytes, filename: str) -> str:
    """Validate file size and type. Returns the detected file type."""
    if len(data) > MAX_FILE_SIZE:
        raise DocumentParseError(
            f"File too large: {len(data)} bytes (max {MAX_FILE_SIZE})"
        )

    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_TYPES:
        raise DocumentParseError(f"Unsupported file type: .{ext}")

    # Magic byte check
    if ext == "pdf" and not data[:4].startswith(PDF_MAGIC):
        raise DocumentParseError("File does not appear to be a valid PDF")

    if ext == "docx" and not data[:4].startswith(DOCX_MAGIC):
        raise DocumentParseError("File does not appear to be a valid DOCX")

    return ext


def extract_text_from_pdf(data: bytes) -> str:
    """Extract text from a PDF file using PyMuPDF."""
    import fitz  # PyMuPDF

    doc = fitz.open(stream=data, filetype="pdf")
    text_parts = []

    for page in doc:
        text = page.get_text()
        if text.strip():
            text_parts.append(text.strip())

    doc.close()

    result = "\n\n".join(text_parts)
    logger.info("pdf_text_extracted", chars=len(result))
    return result


def extract_text_from_docx(data: bytes) -> str:
    """Extract text from a DOCX file using python-docx."""
    from docx import Document

    doc = Document(io.BytesIO(data))
    text_parts = []

    for paragraph in doc.paragraphs:
        if paragraph.text.strip():
            text_parts.append(paragraph.text.strip())

    result = "\n\n".join(text_parts)
    logger.info("docx_text_extracted", chars=len(result))
    return result


def parse_document(data: bytes, filename: str) -> str:
    """Parse a document and extract its text content.

    Validates file type and size before processing.
    """
    file_type = validate_file(data, filename)

    if file_type == "pdf":
        return extract_text_from_pdf(data)
    elif file_type == "docx":
        return extract_text_from_docx(data)
    else:
        raise DocumentParseError(f"Unsupported file type: {file_type}")
