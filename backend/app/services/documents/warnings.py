"""
SOC 2 control exceptions as "Document warnings".

Exceptions never lower a score (spec decisions log). They are shown to the
client as warnings with file and page instead, and a client can dismiss one.
warning_key identifies a warning across re-parses and re-runs, so a
dismissal sticks: it is a hash of the document id, control, page and
description, with case and whitespace ignored.

Pure: takes documents (VendorDocument rows or dicts with the same keys) and
touches no database. Used by the questionnaire (task 4) and the evaluation
(task 5).
"""

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel


class DocumentWarning(BaseModel):
    warning_key: str
    document_id: str
    filename: str
    page: int | None
    control: str
    description: str


def _get(document: Any, name: str) -> Any:
    if isinstance(document, Mapping):
        return document.get(name)
    return getattr(document, name, None)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def warning_key(
    document_id: str, control: str, page: int | None, description: str
) -> str:
    """First 16 hex characters of sha256(document_id|control|page|description)."""
    page_part = "" if page is None else str(page)
    raw = f"{document_id}|{_norm(control)}|{page_part}|{_norm(description)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def document_warnings(documents: Iterable[Any]) -> list[DocumentWarning]:
    """
    One warning per control exception in each parsed SOC 2 document, in
    document order and then report order. Exceptions count whatever the
    report's tier. Documents that are not parsed SOC 2 reports are skipped,
    and so are duplicate exceptions within one document.
    """
    warnings: list[DocumentWarning] = []
    seen: set[str] = set()
    for document in documents:
        status = _get(document, "parse_status")
        extracted = _get(document, "extracted")
        if status not in (None, "parsed") or not isinstance(extracted, Mapping):
            continue
        if extracted.get("kind") != "soc2":
            continue
        fields = extracted.get("fields")
        exceptions = (
            fields.get("control_exceptions") if isinstance(fields, Mapping) else None
        )
        if not isinstance(exceptions, list):
            continue

        document_id = str(_get(document, "id"))
        filename = str(_get(document, "filename") or "")
        for item in exceptions:
            if not isinstance(item, Mapping):
                continue
            control = str(item.get("control") or "").strip()
            description = str(item.get("description") or "").strip()
            if not control and not description:
                continue
            page = item.get("page") if isinstance(item.get("page"), int) else None
            key = warning_key(document_id, control, page, description)
            if key in seen:
                continue
            seen.add(key)
            warnings.append(
                DocumentWarning(
                    warning_key=key,
                    document_id=document_id,
                    filename=filename,
                    page=page,
                    control=control,
                    description=description,
                )
            )
    return warnings
