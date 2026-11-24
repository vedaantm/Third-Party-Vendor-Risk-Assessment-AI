"""Text per page from a PDF, with pypdf. No OCR: scanned PDFs have no text."""

import io
import re

from pypdf import PdfReader

PDF_MAGIC = b"%PDF-"


class DocumentTextError(ValueError):
    """The file can't be read as a PDF with text in it."""


def is_pdf(data: bytes) -> bool:
    # The header may follow a little junk; readers accept it in the first 1 KiB
    return PDF_MAGIC in data[:1024]


def extract_pages(data: bytes) -> list[str]:
    """One string per page, whitespace tidied. Raises DocumentTextError."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentTextError("the PDF is password-protected")
        pages = [_tidy(page.extract_text() or "") for page in reader.pages]
    except DocumentTextError:
        raise
    except Exception as e:  # pypdf raises many types for a broken file
        raise DocumentTextError(
            f"the PDF could not be read ({type(e).__name__})"
        ) from e
    if not any(pages):
        raise DocumentTextError(
            "the PDF has no extractable text (scanned PDFs need OCR, not supported yet)"
        )
    return pages


def _tidy(text: str) -> str:
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)
