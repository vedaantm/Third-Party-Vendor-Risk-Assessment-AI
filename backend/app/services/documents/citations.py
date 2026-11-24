"""
Check the model's citations against the document text.

For every cited value, the quote must appear (whitespace and case aside) on
the page given. If it's on a different page, the page is corrected; if it's
nowhere, the citation is marked unverified. The model can misread a page
number; it can't invent a quote that survives this check.
"""

import re
from typing import Any

MAX_QUOTE_CHARS = 300


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _locate(quote: str, page: int | None, pages: list[str]) -> int | None:
    """The 1-based page the quote is on, preferring the cited one."""
    needle = _norm(quote)
    if not needle:
        return None
    normed = [_norm(p) for p in pages]
    if page is not None and 1 <= page <= len(pages) and needle in normed[page - 1]:
        return page
    for number, text in enumerate(normed, start=1):
        if needle in text:
            return number
    return None


def verify_citation(cited: dict[str, Any], pages: list[str]) -> dict[str, Any]:
    """Return the citation with a checked page and a `verified` flag."""
    out = dict(cited)
    quote = out.get("quote")
    if isinstance(quote, str):
        quote = quote.strip()[:MAX_QUOTE_CHARS]
        out["quote"] = quote or None
    page = out.get("page") if isinstance(out.get("page"), int) else None
    found = _locate(quote, page, pages) if isinstance(quote, str) else None
    out["verified"] = found is not None
    if found is not None:
        out["page"] = found
    elif page is not None and not 1 <= page <= len(pages):
        out["page"] = None  # a page the document doesn't have
    return out


def _is_citation(value: Any) -> bool:
    return isinstance(value, dict) and "page" in value and "quote" in value


def verify_all(extracted: dict[str, Any], pages: list[str]) -> dict[str, Any]:
    """Verify every citation in a dumped Soc2Report / Iso27001Certificate."""
    out: dict[str, Any] = {}
    for key, value in extracted.items():
        if _is_citation(value):
            out[key] = verify_citation(value, pages)
        elif isinstance(value, list) and all(_is_citation(v) for v in value):
            out[key] = [verify_citation(v, pages) for v in value]
        else:
            out[key] = value
    return out
