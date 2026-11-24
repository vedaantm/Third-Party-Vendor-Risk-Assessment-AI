# backend/app/services/documents/pipeline.py

"""
Parse one uploaded vendor document: read its text, decide what it is, have
the LLM extract the facts, check them, and give it an evidence tier.

    pages = text.extract_pages(file)          pypdf, no OCR
    kind  = classify.classify(pages)          soc2 / iso27001 / other (code)
    facts = extraction.extract(kind, pages)   LLM, structured output
    facts = citations.verify_all(facts)       quotes matched to the pages (code)
    flags = injection.find_instructions(...)  text addressed to an AI model (code)
    tier  = assessment.assess_*(facts)        the rules in code, never the model

Runs as a background task after the upload, with its own database session.
The outcome is written to the document row (parse_status, kind, extracted,
evidence_tier, parse_error). Failures are recorded there and logged, never
raised: the upload request has already returned.
"""

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.db.models import VendorDocument
from app.log import get_logger
from app.services.documents import assessment, citations, extraction
from app.services.documents.classify import classify
from app.services.documents.injection import find_instructions
from app.services.documents.text import DocumentTextError, extract_pages, is_pdf
from app.settings import settings

log = get_logger(__name__)

MAX_ERROR_CHARS = 500


class ParseError(RuntimeError):
    """A failure whose message is safe to show the client."""


def _today() -> date:
    """Separate so tests can fix the date."""
    return datetime.now(UTC).date()


def _read_file(storage_key: str) -> bytes:
    root = Path(settings.document_storage_dir).resolve()
    path = (root / storage_key).resolve()
    if not path.is_relative_to(root):
        raise ParseError(
            "the document's storage key points outside the storage directory"
        )
    if not path.is_file():
        raise ParseError("the uploaded file is missing from storage")
    return path.read_bytes()


async def analyse(data: bytes, filename: str, today: date) -> dict[str, Any]:
    """
    The whole parse, without the database:
    {"kind", "evidence_tier", "extracted"}. Raises ParseError,
    DocumentTextError or extraction errors.
    """
    if not is_pdf(data):
        return {
            "kind": "other",
            "evidence_tier": None,
            "extracted": {"kind": "other", "reason": "not a PDF; stored, not parsed"},
        }
    pages = await asyncio.to_thread(extract_pages, data)
    kind = classify(pages)
    if kind == "other":
        return {
            "kind": "other",
            "evidence_tier": None,
            "extracted": {
                "kind": "other",
                "page_count": len(pages),
                "reason": "not recognised as a SOC 2 report or an ISO 27001 certificate",
            },
        }

    result = await extraction.extract(kind, pages, filename)
    fields = citations.verify_all(result["fields"], pages)
    suspicious = find_instructions(pages)
    if kind == "soc2":
        verdict = assessment.assess_soc2(fields, today, suspicious)
    else:
        verdict = assessment.assess_iso27001(fields, today, suspicious)
    return {
        "kind": kind,
        "evidence_tier": verdict["tier"],
        "extracted": {
            "kind": kind,
            "page_count": len(pages),
            "prompt": result["prompt"],
            "truncated": result["truncated"],
            "fields": fields,
            "assessment": verdict,
            "suspicious_instructions": suspicious,
        },
    }


async def _set(document_id: str, **values: Any) -> None:
    async with AsyncSessionLocal() as db:
        doc = (
            await db.execute(
                select(VendorDocument).where(VendorDocument.id == document_id)
            )
        ).scalar_one_or_none()
        if doc is None:
            return
        for key, value in values.items():
            setattr(doc, key, value)
        await db.commit()


async def parse_document(document_id: str) -> None:
    """Parse one uploaded vendor document and store the outcome on its row."""
    try:
        async with AsyncSessionLocal() as db:
            doc = (
                await db.execute(
                    select(VendorDocument).where(VendorDocument.id == document_id)
                )
            ).scalar_one_or_none()
            if doc is None:
                log.warning("documents.parse_missing", document_id=document_id)
                return
            doc.parse_status, doc.parse_error = "parsing", None
            storage_key, filename = doc.storage_key, doc.filename
            await db.commit()

        data = await asyncio.to_thread(_read_file, storage_key)
        outcome = await analyse(data, filename, _today())
        await _set(
            document_id,
            parse_status="parsed",
            parse_error=None,
            kind=outcome["kind"],
            evidence_tier=outcome["evidence_tier"],
            extracted=outcome["extracted"],
        )
        log.info(
            "documents.parsed",
            document_id=document_id,
            kind=outcome["kind"],
            evidence_tier=outcome["evidence_tier"],
        )
    except Exception as e:
        # Known failures carry a message for the client; anything else only
        # its type, so internals and provider responses never reach the UI.
        if isinstance(e, ParseError | DocumentTextError | extraction.ExtractionError):
            message = str(e)
        else:
            message = f"the document couldn't be parsed ({type(e).__name__})"
        log.exception("documents.parse_failed", document_id=document_id)
        try:
            await _set(
                document_id,
                parse_status="failed",
                parse_error=message[:MAX_ERROR_CHARS],
                evidence_tier=None,
            )
        except Exception:
            log.exception(
                "documents.parse_failure_not_recorded", document_id=document_id
            )
