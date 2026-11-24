"""
The SOC 2 / ISO 27001 document parser (app/services/documents).

Every document is a synthetic fixture from tests/fixtures/documents, and the
LLM is a fake that returns that fixture's prepared extraction (or whatever a
test hands it). The evidence tier is always decided by code, so most tests
call the rules directly with a fixed date.
"""

import asyncio
import copy
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FIXTURES, make_sessionmaker
from langchain_core.messages import HumanMessage, SystemMessage

from app.db.models import VendorDocument, VendorEngagement
from app.prompts import load_prompt
from app.services.documents import assessment, citations, extraction, pipeline
from app.services.documents.classify import classify
from app.services.documents.injection import find_instructions
from app.services.documents.schemas import Iso27001Certificate, Soc2Report
from app.services.documents.text import DocumentTextError, extract_pages, is_pdf
from app.settings import settings

DOCS = FIXTURES / "documents"
TODAY = date(2026, 11, 23)  # every fixture's dates are relative to this
ORG = str(settings.default_org_id)


def pdf(name: str) -> bytes:
    return (DOCS / f"{name}.pdf").read_bytes()


def pages_of(name: str) -> list[str]:
    return extract_pages(pdf(name))


def extraction_of(name: str) -> dict:
    return json.loads((DOCS / f"{name}.extraction.json").read_text(encoding="utf-8"))


def verified(name: str, **overrides) -> dict:
    """The fixture's extraction after the citation check, with fields replaced."""
    fields = {**extraction_of(name), **overrides}
    return citations.verify_all(fields, pages_of(name))


# ── Fake LLM ──────────────────────────────────────────────────────────────


class FakeLLM:
    """
    Like LangChain's with_structured_output(include_raw=True): the reply is
    parsed into the schema, and a mismatch comes back as parsing_error.
    """

    def __init__(self, reply: dict | str | None = None, error: Exception | None = None):
        self.reply, self.error = reply, error
        self.calls: list[list] = []
        self.structured: list[tuple] = []

    def with_structured_output(self, schema, *, method, include_raw):
        self.structured.append((schema, method, include_raw))
        llm = self

        class Structured:
            async def ainvoke(self, messages):
                llm.calls.append(messages)
                if llm.error:
                    raise llm.error
                raw = llm.reply if isinstance(llm.reply, str) else json.dumps(llm.reply)
                try:
                    parsed = schema.model_validate_json(raw)
                except ValueError as e:
                    return {
                        "raw": SimpleNamespace(content=raw),
                        "parsed": None,
                        "parsing_error": e,
                    }
                return {
                    "raw": SimpleNamespace(content=raw),
                    "parsed": parsed,
                    "parsing_error": None,
                }

        return Structured()


@pytest.fixture
def fake_llm(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(extraction, "get_llm", lambda **kwargs: llm)
    return llm


# ── Fixtures are what the generator produces ──────────────────────────────


def test_committed_fixtures_match_the_generator():
    import importlib.util

    spec = importlib.util.spec_from_file_location("generate_docs", DOCS / "generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    files = module.render()
    assert len(files) >= 17
    for filename, data in files.items():
        assert (DOCS / filename).read_bytes() == data, (
            f"{filename} is stale: regenerate it"
        )


def test_every_fixture_is_marked_synthetic():
    for path in DOCS.glob("*.pdf"):
        assert "SYNTHETIC TEST DOCUMENT" in extract_pages(path.read_bytes())[0]


def test_every_prepared_extraction_fits_its_schema():
    for path in DOCS.glob("*.extraction.json"):
        schema = Iso27001Certificate if path.name.startswith("iso") else Soc2Report
        schema.model_validate_json(path.read_text(encoding="utf-8"))


# ── Text ──────────────────────────────────────────────────────────────────


def test_extract_pages_returns_text_per_page():
    pages = pages_of("soc2_type2_clean")
    assert len(pages) == 3
    assert "SOC 2 Type 2 Report" in pages[0]
    assert "Complementary User Entity Controls" in pages[2]


def test_is_pdf():
    assert is_pdf(pdf("soc2_type1"))
    assert not is_pdf(b"PK\x03\x04 a zip file")


def test_broken_pdf_raises_document_text_error():
    with pytest.raises(DocumentTextError, match="could not be read"):
        extract_pages(b"%PDF-1.4\nthis is not really a pdf")


def test_pdf_without_text_raises_document_text_error():
    import importlib.util

    spec = importlib.util.spec_from_file_location("generate_docs", DOCS / "generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    blank = module.build_pdf([[]])
    with pytest.raises(DocumentTextError, match="OCR"):
        extract_pages(blank)


# ── Classification ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name, kind",
    [
        ("soc2_type2_clean", "soc2"),
        ("soc2_type2_exceptions", "soc2"),
        ("soc2_type1", "soc2"),
        ("soc2_stale", "soc2"),
        ("soc2_qualified", "soc2"),
        ("soc2_injection", "soc2"),
        ("iso27001_valid", "iso27001"),
        ("iso27001_expired", "iso27001"),
        ("not_a_report", "other"),
    ],
)
def test_classify_fixtures(name, kind):
    assert classify(pages_of(name)) == kind


def test_a_soc2_report_that_mentions_iso27001_is_still_soc2():
    pages = pages_of("soc2_type2_clean")
    pages[1] += "\nNorthwind also holds an ISO/IEC 27001 certificate."
    assert classify(pages) == "soc2"


def test_a_passing_mention_is_not_enough():
    assert classify(["Our security team is working towards SOC 2."]) == "other"
    assert classify(["We plan to get ISO 27001 next year."]) == "other"


# ── Citations ─────────────────────────────────────────────────────────────


def test_citation_on_the_right_page_is_verified():
    pages = pages_of("soc2_type2_clean")
    out = citations.verify_citation(
        {"value": 2, "page": 1, "quote": "SOC 2 Type 2 Report"}, pages
    )
    assert out == {
        "value": 2,
        "page": 1,
        "quote": "SOC 2 Type 2 Report",
        "verified": True,
    }


def test_citation_ignores_case_and_line_breaks():
    pages = pages_of("soc2_type2_clean")
    # The opinion wraps across lines in the PDF
    quote = "SUITABLY DESIGNED AND OPERATED   effectively throughout the period"
    assert citations.verify_citation({"value": "x", "page": 2, "quote": quote}, pages)[
        "verified"
    ]


def test_citation_on_the_wrong_page_is_moved():
    pages = pages_of("soc2_type2_clean")
    out = citations.verify_citation(
        {"value": 2, "page": 3, "quote": "SOC 2 Type 2 Report"}, pages
    )
    assert out["verified"] and out["page"] == 1


def test_invented_quote_is_unverified_and_bad_page_dropped():
    pages = pages_of("soc2_type2_clean")
    out = citations.verify_citation(
        {"value": 2, "page": 40, "quote": "This is a Type 2 report"}, pages
    )
    assert out["verified"] is False and out["page"] is None


def test_missing_quote_is_unverified():
    out = citations.verify_citation(
        {"value": None, "page": None, "quote": None}, ["text"]
    )
    assert out["verified"] is False


def test_long_quote_is_cut():
    out = citations.verify_citation(
        {"value": "x", "page": 1, "quote": "a" * 1000}, ["a"]
    )
    assert len(out["quote"]) == citations.MAX_QUOTE_CHARS


def test_verify_all_covers_lists_and_bridge_letter():
    fields = verified(
        "soc2_type2_exceptions",
        bridge_letter={
            "covers_through": "2026-11-01",
            "page": 1,
            "quote": "not in the document",
        },
    )
    assert all(e["verified"] for e in fields["control_exceptions"])
    assert [e["page"] for e in fields["control_exceptions"]] == [3, 3]
    assert fields["bridge_letter"]["verified"] is False


# ── SOC 2 rules ───────────────────────────────────────────────────────────


def reason_codes(verdict: dict) -> list[str]:
    return [r["code"] for r in verdict["reasons"]]


def test_clean_type2_is_verified_document():
    verdict = assessment.assess_soc2(verified("soc2_type2_clean"), TODAY)
    assert verdict["tier"] == assessment.VERIFIED_DOCUMENT
    assert verdict["reasons"] == []
    assert verdict["modified_opinion"] is False


def test_exceptions_do_not_lower_the_tier():
    verdict = assessment.assess_soc2(verified("soc2_type2_exceptions"), TODAY)
    assert verdict["tier"] == assessment.VERIFIED_DOCUMENT


def test_type1_is_not_verified():
    verdict = assessment.assess_soc2(verified("soc2_type1"), TODAY)
    assert verdict["tier"] == assessment.UNVERIFIED_DOCUMENT
    assert reason_codes(verdict) == ["type_1"]
    assert "point in time" in verdict["reasons"][0]["message"]


def test_stale_period_is_not_verified():
    verdict = assessment.assess_soc2(verified("soc2_stale"), TODAY)
    assert verdict["tier"] == assessment.UNVERIFIED_DOCUMENT
    assert reason_codes(verdict) == ["stale_period"]
    assert "2024-09-30" in verdict["reasons"][0]["message"]


@pytest.mark.parametrize(
    "age_days, tier",
    [
        (365, assessment.VERIFIED_DOCUMENT),
        (366, assessment.UNVERIFIED_DOCUMENT),
    ],
)
def test_twelve_month_boundary(age_days, tier):
    fields = verified("soc2_type2_clean")
    period_end = date(2026, 9, 30)
    verdict = assessment.assess_soc2(fields, period_end + timedelta(days=age_days))
    assert verdict["tier"] == tier


def test_bridge_letter_covers_a_stale_period():
    pages = pages_of("soc2_stale") + [
        "Bridge letter: no material changes through 2026-06-30."
    ]
    fields = {
        **extraction_of("soc2_stale"),
        "bridge_letter": {
            "covers_through": "2026-06-30",
            "page": 4,
            "quote": "no material changes through 2026-06-30",
        },
    }
    verdict = assessment.assess_soc2(citations.verify_all(fields, pages), TODAY)
    assert verdict["tier"] == assessment.VERIFIED_DOCUMENT


def test_bridge_letter_that_is_also_old_does_not_help():
    pages = pages_of("soc2_stale") + [
        "Bridge letter: no material changes through 2025-03-31."
    ]
    fields = {
        **extraction_of("soc2_stale"),
        "bridge_letter": {
            "covers_through": "2025-03-31",
            "page": 4,
            "quote": "no material changes through 2025-03-31",
        },
    }
    verdict = assessment.assess_soc2(citations.verify_all(fields, pages), TODAY)
    assert reason_codes(verdict) == ["stale_period"]
    assert "only covers to 2025-03-31" in verdict["reasons"][0]["message"]


def test_bridge_letter_not_in_the_document_does_not_help():
    fields = verified(
        "soc2_stale",
        bridge_letter={
            "covers_through": "2026-10-31",
            "page": 1,
            "quote": "bridge letter through October",
        },
    )
    verdict = assessment.assess_soc2(fields, TODAY)
    assert reason_codes(verdict) == ["stale_period"]


def test_bridge_letter_dated_in_the_future_does_not_help():
    pages = pages_of("soc2_stale") + [
        "Bridge letter: no material changes through 2027-06-30."
    ]
    fields = {
        **extraction_of("soc2_stale"),
        "bridge_letter": {
            "covers_through": "2027-06-30",
            "page": 4,
            "quote": "no material changes through 2027-06-30",
        },
    }
    verdict = assessment.assess_soc2(citations.verify_all(fields, pages), TODAY)
    assert reason_codes(verdict) == ["stale_period"]


def test_qualified_opinion_is_not_verified_and_flagged():
    verdict = assessment.assess_soc2(verified("soc2_qualified"), TODAY)
    assert verdict["tier"] == assessment.UNVERIFIED_DOCUMENT
    assert reason_codes(verdict) == ["opinion_modified"]
    assert verdict["modified_opinion"] is True
    assert verdict["opinion"] == "qualified"


@pytest.mark.parametrize("opinion", ["adverse", "disclaimer"])
def test_adverse_and_disclaimed_opinions_are_flagged(opinion):
    fields = verified("soc2_type2_clean")
    fields["opinion"] = {**fields["opinion"], "value": opinion}
    verdict = assessment.assess_soc2(fields, TODAY)
    assert verdict["tier"] == assessment.UNVERIFIED_DOCUMENT
    assert verdict["modified_opinion"] is True


def test_missing_facts_are_not_verified():
    verdict = assessment.assess_soc2(citations.verify_all({}, ["page"]), TODAY)
    assert verdict["tier"] == assessment.UNVERIFIED_DOCUMENT
    assert reason_codes(verdict) == [
        "type_unknown",
        "opinion_unknown",
        "period_unknown",
    ]
    assert verdict["modified_opinion"] is False


def test_period_ending_in_the_future_is_not_verified():
    fields = verified("soc2_type2_clean")
    verdict = assessment.assess_soc2(fields, date(2026, 9, 1))
    assert reason_codes(verdict) == ["period_in_future"]


def test_unquoted_deciding_facts_are_not_verified():
    fields = verified(
        "soc2_type2_clean",
        report_type={
            "value": 2,
            "page": 1,
            "quote": "operating effectiveness was tested",
        },
    )
    verdict = assessment.assess_soc2(fields, TODAY)
    assert reason_codes(verdict) == ["unverified_citation"]
    assert "report_type" in verdict["reasons"][0]["message"]


# ── ISO 27001 rules ───────────────────────────────────────────────────────


def test_valid_iso_certificate_is_corroborated():
    verdict = assessment.assess_iso27001(verified("iso27001_valid"), TODAY)
    assert verdict["tier"] == assessment.CORROBORATED
    assert verdict["reasons"] == []


def test_expired_iso_certificate_is_flagged():
    verdict = assessment.assess_iso27001(verified("iso27001_expired"), TODAY)
    assert verdict["tier"] == assessment.EXPIRED
    assert reason_codes(verdict) == ["expired"]
    assert "2024-01-31" in verdict["reasons"][0]["message"]


def test_iso_certificate_is_valid_on_its_expiry_date():
    verdict = assessment.assess_iso27001(verified("iso27001_valid"), date(2028, 3, 14))
    assert verdict["tier"] == assessment.CORROBORATED
    verdict = assessment.assess_iso27001(verified("iso27001_valid"), date(2028, 3, 15))
    assert verdict["tier"] == assessment.EXPIRED


def test_iso_without_expiry_is_unverified():
    fields = verified(
        "iso27001_valid", expiry_date={"value": None, "page": None, "quote": None}
    )
    verdict = assessment.assess_iso27001(fields, TODAY)
    assert verdict["tier"] == assessment.UNVERIFIED_DOCUMENT
    assert reason_codes(verdict) == ["expiry_unknown"]


def test_iso_with_unquoted_expiry_is_unverified():
    fields = verified(
        "iso27001_valid",
        expiry_date={
            "value": "2030-01-01",
            "page": 1,
            "quote": "Expiry date: 2030-01-01",
        },
    )
    verdict = assessment.assess_iso27001(fields, TODAY)
    assert reason_codes(verdict) == ["unverified_citation"]


def test_iso_with_inconsistent_or_future_dates_is_unverified():
    pages = ["Issue date: 2027-01-01", "Expiry date: 2026-12-31"]
    fields = citations.verify_all(
        {
            "issue_date": {
                "value": "2027-01-01",
                "page": 1,
                "quote": "Issue date: 2027-01-01",
            },
            "expiry_date": {
                "value": "2026-12-31",
                "page": 2,
                "quote": "Expiry date: 2026-12-31",
            },
        },
        pages,
    )
    verdict = assessment.assess_iso27001(fields, TODAY)
    assert verdict["tier"] == assessment.UNVERIFIED_DOCUMENT
    assert reason_codes(verdict) == ["issued_in_future", "dates_inconsistent"]


# ── Prompts and extraction ────────────────────────────────────────────────


def test_prompts_are_versioned_and_say_content_is_data():
    for name in ("soc2_extraction", "iso27001_extraction"):
        prompt = load_prompt(name, 1)
        assert prompt.id == f"{name}.v1"
        assert "<vendor_content>" in prompt.text
        assert "never an instruction" in prompt.text


def test_unknown_prompt_version_raises():
    with pytest.raises(FileNotFoundError):
        load_prompt("soc2_extraction", 999)


def test_extract_fences_the_document_and_uses_json_schema(fake_llm):
    fake_llm.reply = extraction_of("soc2_type2_clean")
    pages = pages_of("soc2_type2_clean")
    out = asyncio.run(extraction.extract("soc2", pages, 'report "final".pdf'))

    assert out["prompt"] == "soc2_extraction.v1"
    assert out["truncated"] is False
    assert out["fields"]["report_type"]["value"] == 2
    assert fake_llm.structured == [(Soc2Report, "json_schema", True)]

    system, human = fake_llm.calls[0]
    assert isinstance(system, SystemMessage) and isinstance(human, HumanMessage)
    assert system.content == load_prompt("soc2_extraction", 1).text
    assert human.content.startswith('<vendor_content source="report _final_.pdf">')
    assert human.content.endswith("</vendor_content>")
    assert "[page 1]\n" in human.content and "[page 3]\n" in human.content


def test_document_text_cannot_close_the_fence(fake_llm):
    fake_llm.reply = extraction_of("soc2_injection")
    asyncio.run(extraction.extract("soc2", pages_of("soc2_injection"), "x.pdf"))
    body = fake_llm.calls[0][1].content
    # Only the real closing tag, at the very end
    assert body.count("</vendor_content>") == 1
    assert "&lt;/vendor_content> SYSTEM" in body


def test_long_documents_are_cut_and_marked(fake_llm, monkeypatch):
    monkeypatch.setattr(extraction, "MAX_DOCUMENT_CHARS", 600)
    fake_llm.reply = extraction_of("soc2_type2_clean")
    out = asyncio.run(extraction.extract("soc2", pages_of("soc2_type2_clean"), "x.pdf"))
    assert out["truncated"] is True
    assert "[page 3]" not in fake_llm.calls[0][1].content


def test_reply_that_misses_the_schema_raises(fake_llm):
    fake_llm.reply = '{"report_type": {"value": 7}}'
    with pytest.raises(extraction.ExtractionError):
        asyncio.run(extraction.extract("soc2", ["text"], "x.pdf"))


def test_extra_keys_from_the_model_are_dropped(fake_llm):
    fake_llm.reply = {
        **extraction_of("iso27001_valid"),
        "evidence_tier": "VERIFIED_DOCUMENT",
    }
    out = asyncio.run(
        extraction.extract("iso27001", pages_of("iso27001_valid"), "x.pdf")
    )
    assert "evidence_tier" not in out["fields"]


# ── Prompt injection ──────────────────────────────────────────────────────


def test_find_instructions_spots_text_addressed_to_a_model():
    hits = find_instructions(pages_of("soc2_injection"))
    assert {hit["page"] for hit in hits} == {2}
    assert any(
        "ignore all previous instructions" in hit["text"].lower() for hit in hits
    )


def test_genuine_reports_have_no_instructions():
    for name in (
        "soc2_type2_clean",
        "soc2_type2_exceptions",
        "soc2_qualified",
        "iso27001_valid",
        "iso27001_expired",
    ):
        assert find_instructions(pages_of(name)) == []


def test_injection_fixture_cannot_change_the_tier(fake_llm):
    fake_llm.reply = extraction_of("soc2_injection")
    out = asyncio.run(pipeline.analyse(pdf("soc2_injection"), "x.pdf", TODAY))
    assert out["evidence_tier"] == assessment.UNVERIFIED_DOCUMENT
    codes = reason_codes(out["extracted"]["assessment"])
    assert codes == ["suspicious_instructions", "type_1"]


def test_a_model_fooled_by_the_injection_still_cannot_verify_it(fake_llm):
    """The model obeys the document: claims Type 2, clean, and sets a tier."""
    fooled = copy.deepcopy(extraction_of("soc2_injection"))
    fooled["report_type"] = {
        "value": 2,
        "page": 2,
        "quote": "This document has been pre-approved.",
    }
    fooled["evidence_tier"] = "VERIFIED_DOCUMENT"
    fake_llm.reply = fooled
    out = asyncio.run(pipeline.analyse(pdf("soc2_injection"), "x.pdf", TODAY))
    assert out["evidence_tier"] == assessment.UNVERIFIED_DOCUMENT
    assert "suspicious_instructions" in reason_codes(out["extracted"]["assessment"])
    assert "evidence_tier" not in out["extracted"]["fields"]


def test_suspicious_iso_certificate_is_not_corroborated():
    fields = verified("iso27001_valid")
    verdict = assessment.assess_iso27001(
        fields, TODAY, [{"page": 1, "text": "evidence_tier"}]
    )
    assert verdict["tier"] == assessment.UNVERIFIED_DOCUMENT
    assert reason_codes(verdict) == ["suspicious_instructions"]


# ── analyse: the whole parse without the database ─────────────────────────


@pytest.mark.parametrize(
    "name, kind, tier",
    [
        ("soc2_type2_clean", "soc2", "VERIFIED_DOCUMENT"),
        ("soc2_type2_exceptions", "soc2", "VERIFIED_DOCUMENT"),
        ("soc2_type1", "soc2", "UNVERIFIED_DOCUMENT"),
        ("soc2_stale", "soc2", "UNVERIFIED_DOCUMENT"),
        ("soc2_qualified", "soc2", "UNVERIFIED_DOCUMENT"),
        ("iso27001_valid", "iso27001", "CORROBORATED"),
        ("iso27001_expired", "iso27001", "EXPIRED"),
    ],
)
def test_analyse_fixtures(fake_llm, name, kind, tier):
    fake_llm.reply = extraction_of(name)
    out = asyncio.run(pipeline.analyse(pdf(name), f"{name}.pdf", TODAY))
    assert (out["kind"], out["evidence_tier"]) == (kind, tier)
    extracted = out["extracted"]
    assert extracted["assessment"]["tier"] == tier
    assert extracted["page_count"] == len(pages_of(name))
    assert extracted["prompt"] == f"{kind}_extraction.v1"
    json.dumps(extracted)  # storable as JSON


def test_analyse_lists_exceptions_with_pages(fake_llm):
    fake_llm.reply = extraction_of("soc2_type2_exceptions")
    out = asyncio.run(pipeline.analyse(pdf("soc2_type2_exceptions"), "x.pdf", TODAY))
    exceptions = out["extracted"]["fields"]["control_exceptions"]
    assert [(e["control"], e["page"], e["verified"]) for e in exceptions] == [
        ("CC6.2", 3, True),
        ("CC7.3", 3, True),
    ]


def test_analyse_other_documents_skip_the_llm(fake_llm):
    out = asyncio.run(pipeline.analyse(pdf("not_a_report"), "news.pdf", TODAY))
    assert out == {
        "kind": "other",
        "evidence_tier": None,
        "extracted": {
            "kind": "other",
            "page_count": 1,
            "reason": "not recognised as a SOC 2 report or an ISO 27001 certificate",
        },
    }
    assert fake_llm.calls == []


def test_analyse_non_pdf_is_stored_not_parsed(fake_llm):
    out = asyncio.run(pipeline.analyse(b"PK\x03\x04", "x.zip", TODAY))
    assert out["kind"] == "other" and out["evidence_tier"] is None
    assert fake_llm.calls == []


# ── parse_document: the background task, against a temporary database ────


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / "documents"
    root.mkdir()
    monkeypatch.setattr(settings, "document_storage_dir", str(root))
    return root


@pytest.fixture
def Session(db_url, monkeypatch):
    engine, Session = asyncio.run(make_sessionmaker(db_url))
    monkeypatch.setattr(pipeline, "AsyncSessionLocal", Session)
    monkeypatch.setattr(pipeline, "_today", lambda: TODAY)
    yield Session
    asyncio.run(engine.dispose())


def add_document(
    Session, storage: Path, data: bytes | None, *, storage_key=None
) -> str:
    async def go():
        async with Session() as db:
            eng = VendorEngagement(
                org_id=ORG,
                vendor_name="Northwind",
                company_name="Acme",
                invite_token_hash="0" * 64,
                invite_expires_at=datetime.now(UTC) + timedelta(days=1),
            )
            db.add(eng)
            await db.flush()
            doc = VendorDocument(
                org_id=ORG,
                engagement_id=eng.id,
                filename="report.pdf",
                content_type="application/pdf",
                size_bytes=len(data or b""),
                sha256="0" * 64,
                storage_key=storage_key or f"{ORG}/{eng.id}/doc.pdf",
            )
            db.add(doc)
            await db.commit()
            if data is not None:
                path = storage / doc.storage_key
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            return doc.id

    return asyncio.run(go())


def load(Session, document_id: str) -> VendorDocument:
    async def go():
        async with Session() as db:
            return await db.get(VendorDocument, document_id)

    return asyncio.run(go())


def test_parse_document_stores_the_outcome(Session, storage, fake_llm):
    fake_llm.reply = extraction_of("soc2_type2_clean")
    doc_id = add_document(Session, storage, pdf("soc2_type2_clean"))

    asyncio.run(pipeline.parse_document(doc_id))

    doc = load(Session, doc_id)
    assert doc.parse_status == "parsed" and doc.parse_error is None
    assert doc.kind == "soc2"
    assert doc.evidence_tier == "VERIFIED_DOCUMENT"
    assert doc.extracted["assessment"]["tier"] == "VERIFIED_DOCUMENT"
    assert doc.extracted["fields"]["auditor_firm"]["value"] == "Halcyon & Reed LLP"
    assert doc.extracted["fields"]["opinion"]["page"] == 2


def test_parse_document_iso_expired(Session, storage, fake_llm):
    fake_llm.reply = extraction_of("iso27001_expired")
    doc_id = add_document(Session, storage, pdf("iso27001_expired"))
    asyncio.run(pipeline.parse_document(doc_id))
    doc = load(Session, doc_id)
    assert (doc.kind, doc.evidence_tier, doc.parse_status) == (
        "iso27001",
        "EXPIRED",
        "parsed",
    )


def test_parse_document_other_kind(Session, storage, fake_llm):
    doc_id = add_document(Session, storage, pdf("not_a_report"))
    asyncio.run(pipeline.parse_document(doc_id))
    doc = load(Session, doc_id)
    assert (doc.kind, doc.evidence_tier, doc.parse_status) == ("other", None, "parsed")


def test_parse_document_records_a_missing_file(Session, storage, fake_llm):
    doc_id = add_document(Session, storage, None)
    asyncio.run(pipeline.parse_document(doc_id))
    doc = load(Session, doc_id)
    assert doc.parse_status == "failed"
    assert doc.parse_error == "the uploaded file is missing from storage"
    assert doc.evidence_tier is None


def test_parse_document_refuses_keys_outside_storage(Session, storage, fake_llm):
    outside = storage.parent / "outside.pdf"
    outside.write_bytes(pdf("soc2_type2_clean"))
    doc_id = add_document(Session, storage, None, storage_key="../outside.pdf")
    asyncio.run(pipeline.parse_document(doc_id))
    doc = load(Session, doc_id)
    assert doc.parse_status == "failed"
    assert "outside the storage directory" in doc.parse_error
    assert fake_llm.calls == []


def test_parse_document_records_unreadable_pdf(Session, storage, fake_llm):
    doc_id = add_document(Session, storage, b"%PDF-1.4\ngarbage")
    asyncio.run(pipeline.parse_document(doc_id))
    doc = load(Session, doc_id)
    assert doc.parse_status == "failed"
    assert "could not be read" in doc.parse_error


def test_parse_document_records_schema_mismatch(Session, storage, fake_llm):
    fake_llm.reply = "not json"
    doc_id = add_document(Session, storage, pdf("soc2_type1"))
    asyncio.run(pipeline.parse_document(doc_id))
    doc = load(Session, doc_id)
    assert doc.parse_status == "failed"
    assert doc.parse_error == "the model's reply didn't match the extraction schema"


def test_parse_document_hides_internal_error_details(Session, storage, fake_llm):
    fake_llm.error = RuntimeError("groq said: secret internal detail")
    doc_id = add_document(Session, storage, pdf("soc2_type1"))
    asyncio.run(pipeline.parse_document(doc_id))  # never raises
    doc = load(Session, doc_id)
    assert doc.parse_status == "failed"
    assert doc.parse_error == "the document couldn't be parsed (RuntimeError)"


def test_parse_document_clears_an_earlier_error(Session, storage, fake_llm):
    fake_llm.error = RuntimeError("rate limited")
    doc_id = add_document(Session, storage, pdf("soc2_type2_clean"))
    asyncio.run(pipeline.parse_document(doc_id))
    fake_llm.error, fake_llm.reply = None, extraction_of("soc2_type2_clean")
    asyncio.run(pipeline.parse_document(doc_id))
    doc = load(Session, doc_id)
    assert (doc.parse_status, doc.parse_error, doc.evidence_tier) == (
        "parsed",
        None,
        "VERIFIED_DOCUMENT",
    )


def test_parse_document_unknown_id_does_nothing(Session, storage, fake_llm):
    asyncio.run(pipeline.parse_document("00000000-0000-0000-0000-000000000000"))
    assert fake_llm.calls == []


def test_parse_document_survives_a_database_outage(monkeypatch, fake_llm):
    class Broken:
        def __call__(self):
            raise ConnectionError("database down")

    monkeypatch.setattr(pipeline, "AsyncSessionLocal", Broken())
    asyncio.run(pipeline.parse_document("00000000-0000-0000-0000-000000000000"))
