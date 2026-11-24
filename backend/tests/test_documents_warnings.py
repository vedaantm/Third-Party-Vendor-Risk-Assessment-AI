"""document_warnings: SOC 2 control exceptions as stable, dismissable warnings."""

import asyncio
import copy
import hashlib
from datetime import date
from types import SimpleNamespace

from test_documents_parser import DOCS, FakeLLM, extraction_of

from app.services.documents import extraction, pipeline
from app.services.documents.warnings import (
    DocumentWarning,
    document_warnings,
    warning_key,
)

TODAY = date(2026, 11, 23)
DOC_ID = "11111111-1111-1111-1111-111111111111"
CC62 = "For 2 of 25 terminated users sampled, access was removed after 3 days."


def parsed(name: str, monkeypatch, *, doc_id=DOC_ID, filename=None) -> dict:
    """A document dict as the parser stores it, from a fixture."""
    llm = FakeLLM(extraction_of(name))
    monkeypatch.setattr(extraction, "get_llm", lambda **kwargs: llm)
    data = (DOCS / f"{name}.pdf").read_bytes()
    out = asyncio.run(pipeline.analyse(data, f"{name}.pdf", TODAY))
    return {
        "id": doc_id,
        "filename": filename or f"{name}.pdf",
        "parse_status": "parsed",
        "kind": out["kind"],
        "extracted": out["extracted"],
    }


def test_exceptions_become_warnings_with_file_and_page(monkeypatch):
    warnings = document_warnings([parsed("soc2_type2_exceptions", monkeypatch)])
    assert [(w.control, w.page, w.filename, w.document_id) for w in warnings] == [
        ("CC6.2", 3, "soc2_type2_exceptions.pdf", DOC_ID),
        ("CC7.3", 3, "soc2_type2_exceptions.pdf", DOC_ID),
    ]
    assert warnings[0].description == CC62
    assert all(isinstance(w, DocumentWarning) for w in warnings)


def test_key_is_the_documented_hash():
    expected = hashlib.sha256(
        f"{DOC_ID}|cc6.2|3|access was late.".encode()
    ).hexdigest()[:16]
    assert warning_key(DOC_ID, "CC6.2", 3, "Access was late.") == expected
    assert len(expected) == 16


def test_key_is_stable_across_reparses_and_runs(monkeypatch):
    first = document_warnings([parsed("soc2_type2_exceptions", monkeypatch)])
    second = document_warnings([parsed("soc2_type2_exceptions", monkeypatch)])
    assert [w.warning_key for w in first] == [w.warning_key for w in second]
    # Pinned: this changes only if the key formula changes
    raw = f"{DOC_ID}|cc6.2|3|{CC62.lower()}"
    assert first[0].warning_key == hashlib.sha256(raw.encode()).hexdigest()[:16]


def test_key_ignores_case_and_whitespace():
    assert warning_key(DOC_ID, "CC6.2", 3, "Access  was\nlate.") == warning_key(
        DOC_ID, " cc6.2", 3, "access was late. "
    )


def test_key_differs_by_document_control_page_and_description():
    keys = {
        warning_key(DOC_ID, "CC6.2", 3, "late"),
        warning_key("22222222-2222-2222-2222-222222222222", "CC6.2", 3, "late"),
        warning_key(DOC_ID, "CC6.3", 3, "late"),
        warning_key(DOC_ID, "CC6.2", 4, "late"),
        warning_key(DOC_ID, "CC6.2", None, "late"),
        warning_key(DOC_ID, "CC6.2", 3, "early"),
    }
    assert len(keys) == 6


def test_clean_reports_and_iso_certificates_have_no_warnings(monkeypatch):
    docs = [
        parsed("soc2_type2_clean", monkeypatch),
        parsed("iso27001_valid", monkeypatch, doc_id="i"),
    ]
    assert document_warnings(docs) == []


def test_exceptions_count_whatever_the_tier(monkeypatch):
    doc = parsed("soc2_type2_exceptions", monkeypatch)
    doc["extracted"]["assessment"]["tier"] = "UNVERIFIED_DOCUMENT"
    assert len(document_warnings([doc])) == 2


def test_unparsed_failed_and_other_documents_are_skipped(monkeypatch):
    good = parsed("soc2_type2_exceptions", monkeypatch)
    docs = [
        {**good, "parse_status": "failed"},
        {**good, "parse_status": "pending", "extracted": None},
        {
            "id": "x",
            "filename": "n.pdf",
            "parse_status": "parsed",
            "extracted": {"kind": "other", "reason": "not recognised"},
        },
        {
            "id": "y",
            "filename": "b.pdf",
            "parse_status": "parsed",
            "extracted": {"kind": "soc2", "fields": {"control_exceptions": "garbage"}},
        },
    ]
    assert document_warnings(docs) == []


def test_accepts_orm_like_objects_and_keeps_order(monkeypatch):
    a = parsed("soc2_type2_exceptions", monkeypatch, doc_id="a", filename="a.pdf")
    b = parsed("soc2_type2_exceptions", monkeypatch, doc_id="b", filename="b.pdf")
    warnings = document_warnings([SimpleNamespace(**a), SimpleNamespace(**b)])
    assert [(w.document_id, w.control) for w in warnings] == [
        ("a", "CC6.2"),
        ("a", "CC7.3"),
        ("b", "CC6.2"),
        ("b", "CC7.3"),
    ]
    assert len({w.warning_key for w in warnings}) == 4


def test_duplicates_and_empty_exceptions_are_dropped(monkeypatch):
    doc = parsed("soc2_type2_exceptions", monkeypatch)
    exceptions = doc["extracted"]["fields"]["control_exceptions"]
    exceptions.append(copy.deepcopy(exceptions[0]))
    exceptions.append({"control": "", "description": "", "page": 1})
    exceptions.append({"control": "CC1.1", "description": "No page.", "page": "three"})
    warnings = document_warnings([doc])
    assert [(w.control, w.page) for w in warnings] == [
        ("CC6.2", 3),
        ("CC7.3", 3),
        ("CC1.1", None),
    ]
