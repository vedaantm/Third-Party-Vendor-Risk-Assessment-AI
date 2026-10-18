"""
Certification discovery: SEC EDGAR, FedRAMP, CSA STAR, the web-search
fallback, the extraction agent's batching/retry, the orchestration in
compliance_discovery, and the shared company-name matcher.

HTTP and the LLM are faked; live tests hit SEC and the FedRAMP dataset.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.services import (
    cert_extraction_agent as cea,
    cert_pipeline,
    compliance_discovery,
    csa_star_connector,
    edgar_pipeline,
    fedramp_connector,
)
from app.services.name_matching import core_tokens, names_exact, names_match, names_overlap


class FakeHTTP:
    """Routes requests.get(url, ...) to canned responses by URL substring."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, *args, **kwargs):
        self.calls.append(url)
        for fragment, response in self.routes.items():
            if fragment in url:
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected URL in test: {url}")


def response(json_data=None, text="", status=200, headers=None, content=b""):
    return SimpleNamespace(
        json=lambda: json_data, text=text, status_code=status,
        headers=headers or {}, content=content or text.encode(),
    )


async def no_sleep(*args, **kwargs):
    return None


# ══════════════════════════════════════════════════════════
# SEC EDGAR
# ══════════════════════════════════════════════════════════

def submissions(name, forms):
    return {
        "name": name,
        "filings": {"recent": {
            "form":            [f for f, _ in forms],
            "filingDate":      ["2026-02-01"] * len(forms),
            "accessionNumber": [acc for _, acc in forms],
            "primaryDocument": [f"doc{i}.htm" for i in range(len(forms))],
        }},
    }


@pytest.fixture
def okta_ticker(monkeypatch):
    monkeypatch.setattr(edgar_pipeline, "_find_ticker", lambda name: "OKTA")
    monkeypatch.setattr(edgar_pipeline, "_load_ticker_cik_map",
                        lambda: {"OKTA": {"cik": 1660134, "title": "Okta, Inc."}})


def test_resolve_cik_via_ticker_picks_latest_annual_filing(monkeypatch, okta_ticker):
    http = FakeHTTP({"CIK0001660134.json": response(submissions(
        "Okta, Inc.", [("8-K", "0001-26-000001"), ("10-K", "0001660134-26-000010"), ("10-K", "old")]))})
    monkeypatch.setattr(edgar_pipeline.requests, "get", http.get)

    cik, name, filing = edgar_pipeline._resolve_cik("Okta")
    assert (cik, name) == ("0001660134", "Okta, Inc.")
    assert filing["form"] == "10-K"
    assert filing["url"] == ("https://www.sec.gov/Archives/edgar/data/"
                             "1660134/000166013426000010/doc1.htm")


def test_resolve_cik_accepts_20f(monkeypatch):
    monkeypatch.setattr(edgar_pipeline, "_find_ticker", lambda name: "INFY")
    monkeypatch.setattr(edgar_pipeline, "_load_ticker_cik_map",
                        lambda: {"INFY": {"cik": 1067491, "title": "Infosys Ltd"}})
    http = FakeHTTP({"CIK0001067491.json": response(submissions("Infosys Ltd", [("20-F", "a-b-c")]))})
    monkeypatch.setattr(edgar_pipeline.requests, "get", http.get)
    assert edgar_pipeline._resolve_cik("Infosys")[2]["form"] == "20-F"


def test_ticker_with_mismatched_title_falls_back_to_name_search(monkeypatch):
    monkeypatch.setattr(edgar_pipeline, "_find_ticker", lambda name: "VNT")
    monkeypatch.setattr(edgar_pipeline, "_load_ticker_cik_map",
                        lambda: {"VNT": {"cik": 1, "title": "Vontier Corp"}})
    http = FakeHTTP({
        "browse-edgar": response(text="<a href='?CIK=0000099999'>"),
        "CIK0000099999.json": response(submissions("Vanta Inc.", [("10-K", "x")])),
    })
    monkeypatch.setattr(edgar_pipeline.requests, "get", http.get)
    cik, name, _ = edgar_pipeline._resolve_cik("Vanta")
    assert (cik, name) == ("0000099999", "Vanta Inc.")
    assert not any("CIK0000000001" in c for c in http.calls)   # mismatched ticker CIK never fetched


def test_registered_name_mismatch_is_rejected(monkeypatch, okta_ticker):
    http = FakeHTTP({
        "CIK0001660134.json": response(submissions("Octa Holdings Ltd", [("10-K", "x")])),
        "browse-edgar": response(text="no results"),
    })
    monkeypatch.setattr(edgar_pipeline.requests, "get", http.get)
    assert edgar_pipeline._resolve_cik("Okta") == (None, None, None)


def test_no_annual_filing_returns_cik_only(monkeypatch, okta_ticker):
    http = FakeHTTP({"CIK0001660134.json": response(submissions("Okta, Inc.", [("8-K", "x")]))})
    monkeypatch.setattr(edgar_pipeline.requests, "get", http.get)
    result = asyncio.run(edgar_pipeline.discover_certifications_from_edgar("Okta"))
    assert result["edgar_cik"] == "0001660134"
    assert result["certifications_found"] == [] and result["edgar_filing_url"] is None


def test_unresolvable_company_returns_empty(monkeypatch):
    monkeypatch.setattr(edgar_pipeline, "_find_ticker", lambda name: "")
    monkeypatch.setattr(edgar_pipeline.requests, "get",
                        FakeHTTP({"browse-edgar": response(text="nothing")}).get)
    result = asyncio.run(edgar_pipeline.discover_certifications_from_edgar("Private Startup"))
    assert result["edgar_cik"] is None and result["certifications_found"] == []


def test_extract_relevant_windows_centres_on_terms_and_merges():
    text = "x" * 3000 + " We maintain ISO 27001 and SOC 2 Type II reports. " + "y" * 3000
    windows = edgar_pipeline._extract_relevant_windows(text)
    assert "ISO 27001" in windows and "SOC 2" in windows
    assert "\n---\n" not in windows          # overlapping windows merged into one
    assert len(windows) <= 2 * edgar_pipeline.WINDOW_RADIUS + 60


def test_extract_relevant_windows_respects_total_cap():
    block = "z" * 2000
    text = block.join(f" FedRAMP{i} " for i in range(30))
    assert len(edgar_pipeline._extract_relevant_windows(text)) <= edgar_pipeline.MAX_TOTAL_CHARS + 200


def test_extract_relevant_windows_without_terms_is_empty():
    assert edgar_pipeline._extract_relevant_windows("revenue grew strongly") == ""


def test_edgar_moves_regulations_out_of_certifications(monkeypatch, okta_ticker):
    http = FakeHTTP({
        "CIK0001660134.json": response(submissions("Okta, Inc.", [("10-K", "0001-26-1")])),
        "Archives/edgar": response(text="<html>We hold ISO 27001 and comply with GDPR and SOX.</html>"),
    })
    monkeypatch.setattr(edgar_pipeline.requests, "get", http.get)
    seen = {}

    async def fake_extract(pages):
        seen["pages"] = pages
        return {"certifications_found": ["ISO27001", "GDPR", "SOX"], "evidence": ["ISO27001 — 10-K"],
                "regulatory_frameworks_disclosed": ["CCPA"], "uncertain": [],
                "explicitly_not_mentioned": []}

    monkeypatch.setattr(cea, "extract_certifications", fake_extract)
    result = asyncio.run(edgar_pipeline.discover_certifications_from_edgar("Okta"))
    assert result["certifications_found"] == ["ISO27001"]
    assert set(result["regulatory_frameworks_disclosed"]) == {"CCPA", "GDPR", "SOX"}
    assert result["edgar_filing_url"].endswith("/doc0.htm")
    assert "ISO 27001" in seen["pages"][0]["text"]


def test_filing_without_compliance_terms_skips_llm(monkeypatch, okta_ticker):
    http = FakeHTTP({
        "CIK0001660134.json": response(submissions("Okta, Inc.", [("10-K", "0001-26-1")])),
        "Archives/edgar": response(text="<html>Revenue grew.</html>"),
    })
    monkeypatch.setattr(edgar_pipeline.requests, "get", http.get)

    async def must_not_run(pages):
        raise AssertionError("LLM should not be called")

    monkeypatch.setattr(cea, "extract_certifications", must_not_run)
    result = asyncio.run(edgar_pipeline.discover_certifications_from_edgar("Okta"))
    assert result["certifications_found"] == [] and len(result["sources_checked"]) == 1


# ══════════════════════════════════════════════════════════
# FedRAMP
# ══════════════════════════════════════════════════════════

FEDRAMP_DATA = [
    {"Cloud_Service_Provider_Name": "Okta", "Cloud_Service_Provider_Package": "Okta Identity Cloud",
     "Designation": "Compliant", "Impact_Level": "Moderate",
     "Original_Authorization_Date": "2017-01-01"},
    {"Cloud_Service_Provider_Name": "ReadyCo", "Cloud_Service_Provider_Package": "ReadyCo Platform",
     "Designation": "FedRAMP Ready"},
    {"Cloud_Service_Provider_Name": "PendingCo", "Cloud_Service_Provider_Package": "PendingCo Suite",
     "Designation": "In Process", "Stage": "In Process"},
    {"Cloud_Service_Provider_Name": "Data Systems Inc", "Cloud_Service_Provider_Package": "DSI Cloud",
     "Designation": "Compliant"},
]


@pytest.fixture
def fedramp_data(monkeypatch):
    monkeypatch.setattr(fedramp_connector, "_fedramp_cache", FEDRAMP_DATA)


@pytest.mark.parametrize("vendor, tier", [
    ("Okta", "VERIFIED_REGISTRY"),
    ("Okta, Inc.", "VERIFIED_REGISTRY"),
    ("ReadyCo", "CORROBORATED"),
    ("PendingCo", "CORROBORATED"),
])
def test_fedramp_tiers(fedramp_data, vendor, tier):
    result = fedramp_connector.check_fedramp(vendor)
    assert result["fedramp_found"] is True and result["fedramp_tier"] == tier


def test_fedramp_evidence_names_package_and_impact_level(fedramp_data):
    evidence = fedramp_connector.check_fedramp("Okta")["fedramp_evidence"]
    assert "Okta Identity Cloud" in evidence and "Moderate" in evidence


@pytest.mark.parametrize("vendor", ["Acme Data Systems", "Oktane Labs", "Unknown Vendor"])
def test_fedramp_no_match(fedramp_data, vendor):
    assert fedramp_connector.check_fedramp(vendor) == {
        "fedramp_tier": None, "fedramp_evidence": None, "fedramp_found": False}


def test_fedramp_download_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(fedramp_connector, "_fedramp_cache", None)
    monkeypatch.setattr(fedramp_connector.requests, "get",
                        FakeHTTP({"fedramp-data": ConnectionError("down")}).get)
    assert fedramp_connector.check_fedramp("Okta")["fedramp_found"] is False


def test_fedramp_data_loaded_once(monkeypatch):
    monkeypatch.setattr(fedramp_connector, "_fedramp_cache", None)
    http = FakeHTTP({"fedramp-data": response({"data": {"Providers": FEDRAMP_DATA}})})
    monkeypatch.setattr(fedramp_connector.requests, "get", http.get)
    fedramp_connector.check_fedramp("Okta")
    fedramp_connector.check_fedramp("ReadyCo")
    assert len(http.calls) == 1


# ══════════════════════════════════════════════════════════
# CSA STAR
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("vendor, tier", [
    ("Okta", "VERIFIED_REGISTRY"),                      # Level 2
    ("Palo Alto Networks", "VERIFIED_REGISTRY"),        # seed entry "Palo Alto"
    ("Tata Consultancy Services Limited", "VERIFIED_REGISTRY"),
    ("Infosys", "CORROBORATED"),                        # Level 1
])
def test_csa_star_seed_tiers(vendor, tier):
    result = csa_star_connector.check_csa_star(vendor)
    assert result["csa_star_found"] and result["csa_star_tier"] == tier
    assert "demo dataset" in result["csa_star_evidence"]


@pytest.mark.parametrize("vendor", ["Tata Motors", "Acme Cloud Services", "Boxwood Partners"])
def test_csa_star_seed_no_false_positives(vendor):
    assert csa_star_connector.check_csa_star(vendor)["csa_star_found"] is False


@pytest.fixture
def csa_live(monkeypatch):
    monkeypatch.setattr(csa_star_connector, "DEMO_MODE", False)
    monkeypatch.setattr(csa_star_connector, "_org_cache", None)
    monkeypatch.setenv("CSA_STAR_API_KEY", "test-key")


def csa_http(entry_type):
    return FakeHTTP({
        "/organizations/42": response({"cloud_services": [{"id": 7, "name": "Okta Identity Cloud"}]}),
        "/organizations": response({"organizations": [{"id": 42, "name": "Okta, Inc."}]}),
        "/cloud_services/7": response({"registry_entries": [{"type": entry_type}]}),
    })


@pytest.mark.parametrize("entry_type, tier", [
    ("STARCertification", "VERIFIED_REGISTRY"),
    ("STARAttestation", "VERIFIED_REGISTRY"),
    ("SelfAssessment", "CORROBORATED"),
])
def test_csa_star_live_api_tiers(monkeypatch, csa_live, entry_type, tier):
    monkeypatch.setattr(csa_star_connector.requests, "get", csa_http(entry_type).get)
    assert csa_star_connector.check_csa_star("Okta")["csa_star_tier"] == tier


def test_csa_star_live_without_key_returns_empty(monkeypatch, csa_live):
    monkeypatch.delenv("CSA_STAR_API_KEY")
    assert csa_star_connector.check_csa_star("Okta")["csa_star_found"] is False


def test_csa_star_live_unknown_entry_type_returns_empty(monkeypatch, csa_live):
    monkeypatch.setattr(csa_star_connector.requests, "get", csa_http("Withdrawn").get)
    assert csa_star_connector.check_csa_star("Okta")["csa_star_found"] is False


# ══════════════════════════════════════════════════════════
# Discovery orchestration (compliance_discovery)
# ══════════════════════════════════════════════════════════

EMPTY_EDGAR = {"certifications_found": [], "evidence": [], "regulatory_frameworks_disclosed": [],
               "edgar_cik": None, "edgar_filing_url": None}
NO_CSA = {"csa_star_found": False, "csa_star_tier": None, "csa_star_evidence": None}
NO_FEDRAMP = {"fedramp_found": False, "fedramp_tier": None, "fedramp_evidence": None}


@pytest.fixture
def discovery(monkeypatch):
    """Configurable fakes for every source; records whether web ran."""
    state = SimpleNamespace(edgar=dict(EMPTY_EDGAR), csa=dict(NO_CSA), fedramp=dict(NO_FEDRAMP),
                            web={"certifications_found": [], "evidence": [],
                                 "regulatory_frameworks_disclosed": [], "sources_checked": []},
                            web_called=False)

    async def edgar(name, ticker=None):
        return state.edgar

    async def web(name):
        state.web_called = True
        return state.web

    monkeypatch.setattr(compliance_discovery, "discover_certifications_from_edgar", edgar)
    monkeypatch.setattr(compliance_discovery, "discover_via_web", web)
    monkeypatch.setattr(compliance_discovery, "check_csa_star", lambda n: state.csa)
    monkeypatch.setattr(compliance_discovery, "check_fedramp", lambda n: state.fedramp)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    return state


def discover(name="Okta"):
    return asyncio.run(compliance_discovery.discover_certifications(name))


def test_registry_tiers_win_over_web(discovery):
    discovery.edgar = {**EMPTY_EDGAR, "certifications_found": ["SOC2_Type2"],
                       "edgar_cik": "0001660134", "edgar_filing_url": "https://sec/10k"}
    discovery.fedramp = {"fedramp_found": True, "fedramp_tier": "VERIFIED_REGISTRY",
                         "fedramp_evidence": "authorized"}
    discovery.web = {"certifications_found": ["SOC2_Type2", "FedRAMP", "ISO27001"], "evidence": [],
                     "regulatory_frameworks_disclosed": [], "sources_checked": ["https://okta.com/trust"]}
    result = discover()
    assert result["certification_evidence_tiers"] == {
        "SOC2_Type2": "VERIFIED_REGISTRY", "FedRAMP": "VERIFIED_REGISTRY", "ISO27001": "CORROBORATED"}
    assert result["edgar_cik"] == "0001660134"
    assert "https://okta.com/trust" in result["sources_checked"]


def test_three_or_more_edgar_certs_skip_web_fallback(discovery):
    discovery.edgar = {**EMPTY_EDGAR, "certifications_found": ["SOC1", "SOC2_Type2", "ISO27001"]}
    discover()
    assert discovery.web_called is False


def test_fewer_than_three_edgar_certs_run_web_fallback(discovery):
    discovery.edgar = {**EMPTY_EDGAR, "certifications_found": ["SOC2_Type2", "ISO27001"]}
    discover()
    assert discovery.web_called is True


def test_regulations_never_get_a_tier(discovery):
    discovery.edgar = {**EMPTY_EDGAR, "certifications_found": ["GDPR"],
                       "regulatory_frameworks_disclosed": ["SOX"]}
    discovery.web = {"certifications_found": ["HIPAA", "CCPA", "PCI_DSS"], "evidence": [],
                     "regulatory_frameworks_disclosed": ["FCPA"], "sources_checked": []}
    result = discover()
    assert result["certification_evidence_tiers"] == {"PCI_DSS": "CORROBORATED"}
    assert result["regulatory_frameworks_disclosed"] == ["FCPA", "SOX"]


def test_csa_and_fedramp_results_are_merged(discovery):
    discovery.csa = {"csa_star_found": True, "csa_star_tier": "CORROBORATED", "csa_star_evidence": "L1"}
    discovery.fedramp = {"fedramp_found": True, "fedramp_tier": "VERIFIED_REGISTRY", "fedramp_evidence": "ok"}
    result = discover()
    assert result["certification_evidence_tiers"] == {"CSA_STAR": "CORROBORATED", "FedRAMP": "VERIFIED_REGISTRY"}
    assert set(result["sources_checked"]) == {"CSA STAR Registry", "FedRAMP Marketplace"}
    assert result["certifications_found"] == list(result["certification_evidence_tiers"])


def test_nothing_found_anywhere(discovery):
    result = discover("Private Startup")
    assert result["certifications_found"] == [] and result["certification_evidence_tiers"] == {}


# ══════════════════════════════════════════════════════════
# Web fallback (cert_pipeline)
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("url, domain, expected", [
    ("https://okta.com/trust", "okta.com", True),
    ("https://www.okta.com/trust", "okta.com", True),
    ("https://trust.okta.com/", "okta.com", True),
    ("https://notokta.com/", "okta.com", False),
    ("https://okta.com.evil.io/", "okta.com", False),
])
def test_domain_matches(url, domain, expected):
    assert cert_pipeline._domain_matches(url, domain) is expected


def fake_yf(info):
    return SimpleNamespace(Ticker=lambda symbol: SimpleNamespace(info=info))


@pytest.mark.parametrize("info, expected", [
    ({"longName": "Okta, Inc.", "website": "https://www.okta.com"}, "okta.com"),
    ({"longName": "Okta, Inc.", "website": ""}, None),
    ({"longName": "Oil Palm Bhd", "website": "https://ovbhd.com"}, None),       # name mismatch
    ({"longName": "Okta, Inc.", "website": "https://identity-cloud.io"}, None),  # domain mismatch
])
def test_company_domain_validation(monkeypatch, info, expected):
    monkeypatch.setattr(cert_pipeline, "_find_ticker", lambda name: "OKTA")
    monkeypatch.setattr(cert_pipeline, "yf", fake_yf(info))
    assert cert_pipeline._get_company_domain("Okta") == expected


def test_company_domain_none_without_ticker(monkeypatch):
    monkeypatch.setattr(cert_pipeline, "_find_ticker", lambda name: "")
    assert cert_pipeline._get_company_domain("Private Startup") is None


class FakeSession:
    def __init__(self, resp):
        self.resp = resp

    def get(self, url, timeout):
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


def test_fetch_page_strips_scripts_styles_nav_footer(monkeypatch):
    html = ("<html><nav>menu</nav><script>evil()</script><style>p{}</style>"
            "<p>We are  ISO 27001 certified.</p><footer>(c)</footer></html>")
    monkeypatch.setattr(cert_pipeline, "_session", FakeSession(response(text=html,
                        headers={"Content-Type": "text/html"})))
    assert cert_pipeline._fetch_page_text("https://okta.com/trust") == "We are ISO 27001 certified."


@pytest.mark.parametrize("resp", [
    response(text="nope", status=404),
    response(headers={"Content-Type": "application/pdf",
                      "Content-Length": str(6 * 1024 * 1024)}, content=b"%PDF"),
    response(headers={"Content-Type": "application/pdf"}, content=b"x" * (5 * 1024 * 1024 + 1)),
    response(headers={"Content-Type": "application/pdf"}, content=b"not really a pdf"),
    ConnectionError("timeout"),
])
def test_fetch_page_returns_empty_for_errors_and_oversized_pdfs(monkeypatch, resp):
    monkeypatch.setattr(cert_pipeline, "_session", FakeSession(resp))
    assert cert_pipeline._fetch_page_text("https://okta.com/report.pdf") == ""


@pytest.fixture
def web_pipeline(monkeypatch):
    state = SimpleNamespace(domain="okta.com", queries=[], fetched=[], extracted=None,
                            results=["https://okta.com/trust", "https://trust.okta.com/soc2",
                                     "https://other.com/okta", "https://okta.com/trust"])
    monkeypatch.setattr(cert_pipeline, "_get_company_domain", lambda name: state.domain)

    def search(query):
        state.queries.append(query)
        return list(state.results)

    def fetch(url):
        state.fetched.append(url)
        return f"text of {url}"

    async def extract(pages):
        state.extracted = pages
        return {"certifications_found": ["ISO27001"], "evidence": [],
                "regulatory_frameworks_disclosed": [], "uncertain": [], "explicitly_not_mentioned": []}

    monkeypatch.setattr(cert_pipeline, "_search_web", search)
    monkeypatch.setattr(cert_pipeline, "_fetch_page_text", fetch)
    monkeypatch.setattr(cert_pipeline, "extract_certifications", extract)
    return state


def test_web_fallback_with_domain_uses_site_queries_and_filters(web_pipeline):
    result = asyncio.run(cert_pipeline.discover_and_extract_certifications("Okta"))
    assert all(q.startswith("site:okta.com") for q in web_pipeline.queries)
    assert web_pipeline.fetched == ["https://okta.com/trust", "https://trust.okta.com/soc2"]
    assert result["certifications_found"] == ["ISO27001"]
    assert result["sources_checked"] == web_pipeline.fetched


def test_web_fallback_without_domain_filters_by_name_token(web_pipeline):
    web_pipeline.domain = None
    asyncio.run(cert_pipeline.discover_and_extract_certifications("Okta"))
    assert not any(q.startswith("site:") for q in web_pipeline.queries)
    assert "https://other.com/okta" in web_pipeline.fetched


def test_web_fallback_caps_pages(web_pipeline):
    web_pipeline.results = [f"https://okta.com/p{i}" for i in range(20)]
    asyncio.run(cert_pipeline.discover_and_extract_certifications("Okta"))
    assert len(web_pipeline.fetched) == cert_pipeline.MAX_PAGES_TO_FETCH


def test_web_fallback_with_no_fetchable_pages(web_pipeline, monkeypatch):
    monkeypatch.setattr(cert_pipeline, "_fetch_page_text", lambda url: "")
    result = asyncio.run(cert_pipeline.discover_and_extract_certifications("Okta"))
    assert result["certifications_found"] == []
    assert result["uncertain"] == ["No pages could be fetched"]
    assert web_pipeline.extracted is None


# ══════════════════════════════════════════════════════════
# Certification extraction agent (batching + retry)
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("message, seconds", [
    ("Please try again in 1.35s.", 1.35),
    ("Please try again in 7m12.5s.", 432.5),
    ("Please try again in 2h3m4s.", 7384.0),
    ("Please try again in 450ms.", 0.45),
    ("Rate limit reached.", 10.0),
])
def test_parse_wait_seconds(message, seconds):
    assert cea._parse_wait_seconds(message) == pytest.approx(seconds)


@pytest.mark.xfail(strict=True, reason=(
    "cert_extraction_agent.py (protected) gap: _parse_wait_seconds searches "
    "the whole error string, so digits followed by h/m/s anywhere — e.g. in "
    "Groq's organization id — are read as hours/minutes. A 7.5s wait is "
    "parsed as over an hour and the batch is dropped as a 'daily limit'. "
    "Desired: parse only the 'try again in …' duration."
))
def test_parse_wait_seconds_ignores_digits_outside_the_retry_phrase():
    message = ("Rate limit reached for model `openai/gpt-oss-20b` in organization "
               "`org_01hx4m2s9q` on tokens per minute (TPM): Limit 8000, Used 7417, "
               "Requested 1584. Please try again in 7.5075s.")
    assert cea._parse_wait_seconds(message) == pytest.approx(7.5075)


@pytest.mark.parametrize("raw", [
    '{"certifications_found": ["ISO27001"]}',
    '```json\n{"certifications_found": ["ISO27001"]}\n```',
    '```\n{"certifications_found": ["ISO27001"]}\n```',
])
def test_parse_llm_output(raw):
    assert cea._parse_llm_output(raw)["certifications_found"] == ["ISO27001"]


def test_parse_llm_output_invalid_returns_empty():
    assert cea._parse_llm_output("no json here") == cea._empty_result()


@pytest.mark.xfail(strict=True, reason=(
    "cert_extraction_agent.py (protected) gap: _parse_llm_output only strips "
    "a fence at the very start, so any preamble before the JSON makes the "
    "whole batch parse as empty and its certifications are silently lost. "
    "Desired: locate the JSON object as base_agent.parse_agent_output does."
))
def test_parse_llm_output_with_preamble():
    raw = 'Here are the findings:\n```json\n{"certifications_found": ["ISO27001"]}\n```'
    assert cea._parse_llm_output(raw)["certifications_found"] == ["ISO27001"]


def test_sanitize_moves_regulations():
    result = cea._sanitize({"certifications_found": ["ISO27001", "GDPR", "SOX"],
                            "regulatory_frameworks_disclosed": ["CCPA"]})
    assert result["certifications_found"] == ["ISO27001"]
    assert set(result["regulatory_frameworks_disclosed"]) == {"CCPA", "GDPR", "SOX"}


def test_merge_deduplicates_across_batches():
    merged = cea._merge([
        {"certifications_found": ["ISO27001", "SOC2_Type2"], "evidence": ["a"],
         "regulatory_frameworks_disclosed": ["GDPR"], "uncertain": ["x"]},
        {"certifications_found": ["SOC2_Type2", "FedRAMP"], "evidence": ["b"],
         "regulatory_frameworks_disclosed": ["GDPR", "CCPA"], "uncertain": []},
    ])
    assert merged["certifications_found"] == ["ISO27001", "SOC2_Type2", "FedRAMP"]
    assert merged["regulatory_frameworks_disclosed"] == ["GDPR", "CCPA"]
    assert merged["evidence"] == ["a", "b"] and merged["uncertain"] == ["x"]


class ScriptedLLM:
    """Returns/raises the scripted items in order on each ainvoke."""

    def __init__(self, script):
        self.script, self.calls = list(script), []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(content=item)


@pytest.fixture
def extraction(monkeypatch):
    state = SimpleNamespace(llm=None, sleeps=[], models=[])

    def get_llm(model_id, temperature):
        state.models.append(model_id)
        return state.llm

    async def sleep(seconds):
        state.sleeps.append(seconds)

    monkeypatch.setattr(cea, "get_llm", get_llm)
    monkeypatch.setattr(cea, "asyncio", SimpleNamespace(sleep=sleep))
    return state


OK = '{"certifications_found": ["ISO27001"], "evidence": [], "regulatory_frameworks_disclosed": []}'
PAGE = {"url": "https://okta.com/trust", "text": "ISO 27001 certified"}


def test_extract_batch_uses_20b_model_and_caps_page_text(extraction):
    extraction.llm = ScriptedLLM([OK])
    asyncio.run(cea._extract_batch([{"url": "u", "text": "a" * 5000}]))
    assert extraction.models == ["openai/gpt-oss-20b"]
    human = extraction.llm.calls[0][1].content
    assert "a" * cea.MAX_CHARS_PER_PAGE in human and "a" * (cea.MAX_CHARS_PER_PAGE + 1) not in human


def test_extract_batch_retries_rate_limit_with_parsed_wait(extraction):
    extraction.llm = ScriptedLLM([RuntimeError("429 rate_limit: Please try again in 2s."), OK])
    result = asyncio.run(cea._extract_batch([PAGE]))
    assert result["certifications_found"] == ["ISO27001"]
    assert extraction.sleeps == [4.0]          # max(2, 3) + 1


def test_extract_batch_daily_limit_returns_empty_without_waiting(extraction):
    extraction.llm = ScriptedLLM([RuntimeError("429 rate_limit: Please try again in 3h2m.")])
    assert asyncio.run(cea._extract_batch([PAGE])) == cea._empty_result()
    assert extraction.sleeps == []


def test_extract_batch_gives_up_after_four_attempts(extraction):
    extraction.llm = ScriptedLLM([RuntimeError("429 rate_limit: try again in 1s")] * 4)
    assert asyncio.run(cea._extract_batch([PAGE])) == cea._empty_result()
    assert len(extraction.llm.calls) == 4


def test_extract_batch_non_rate_limit_error_raises(extraction):
    extraction.llm = ScriptedLLM([ValueError("bad request")])
    with pytest.raises(ValueError):
        asyncio.run(cea._extract_batch([PAGE]))


def test_extract_certifications_batches_pages_in_twos(extraction):
    extraction.llm = ScriptedLLM([OK] * 3)
    pages = [dict(PAGE, url=f"https://okta.com/{i}") for i in range(5)]
    result = asyncio.run(cea.extract_certifications(pages))
    assert len(extraction.llm.calls) == 3
    assert extraction.sleeps == [5, 5]         # cooldown between batches only
    assert result["certifications_found"] == ["ISO27001"]


def test_extract_certifications_no_pages():
    assert asyncio.run(cea.extract_certifications([])) == cea._empty_result()


# ══════════════════════════════════════════════════════════
# Company-name matching (shared by EDGAR / FedRAMP / CSA STAR / OFAC)
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("name, tokens", [
    ("Microsoft Corporation", ["microsoft"]),
    ("AMAZON COM INC", ["amazon", "com"]),
    ("Amazon.com, Inc.", ["amazon", "com"]),
    ("Banco Nacional de Cuba, S.A.", ["banco", "nacional", "de", "cuba"]),
    ("The Boeing Company", ["boeing"]),
    ("CrowdStrike Holdings, Inc.", ["crowdstrike"]),
    ("AT&T Inc.", ["at", "and", "t"]),
    ("Bank of the West", ["bank", "of", "the", "west"]),
    ("", []),
])
def test_core_tokens(name, tokens):
    assert core_tokens(name) == tokens


@pytest.mark.parametrize("query, candidate", [
    ("Microsoft", "MICROSOFT CORP"),
    ("Salesforce", "Salesforce Government Cloud"),
    ("Palo Alto Networks", "Palo Alto"),
    ("Tata Consultancy Services Limited", "Tata Consultancy Services"),
    ("Amazon", "AMAZON COM INC"),
    ("SAP SE", "SAP"),
    ("CrowdStrike", "CrowdStrike Holdings, Inc."),
])
def test_names_match_accepts_real_variants(query, candidate):
    assert names_match(query, candidate)


@pytest.mark.parametrize("query, candidate", [
    ("Acme Data Systems", "Data Systems Inc"),
    ("Acme Data Systems", "Center for Translational Data Science"),
    ("Tata Motors", "Tata Consultancy Services"),
    ("Smart Software Inc", "BMC Software"),
    ("Global Network Solutions", "Juniper Networks"),
    ("Palo Alto Networks", "Aruba Networks"),
    ("Microsoft", ""),
])
def test_names_match_rejects_shared_word_false_positives(query, candidate):
    assert not names_match(query, candidate)


def test_names_match_known_limitation_single_word_prefix():
    """Documents a known limitation: a vendor whose first word is a whole
    registry name still matches it."""
    assert names_match("Box Hill Logistics", "Box Inc.")


def test_names_exact_and_overlap():
    assert names_exact("Rosneft", "ROSNEFT")
    assert not names_exact("Rosneft", "ROSNEFT OIL COMPANY")
    assert names_overlap("Rosneft", "OPEN JOINT-STOCK COMPANY ROSNEFT OIL COMPANY")
    assert not names_overlap("Cimexico Corp", "CIMEX")
    assert not names_overlap("Ocean Star", "STAR OF THE OCEAN")


# ══════════════════════════════════════════════════════════
# Live
# ══════════════════════════════════════════════════════════

@pytest.mark.live
def test_live_edgar_resolves_okta_annual_filing():
    cik, name, filing = edgar_pipeline._resolve_cik("Okta")
    assert cik == "0001660134" and "okta" in name.lower()
    assert filing and filing["form"] in edgar_pipeline.ANNUAL_FILING_FORMS


@pytest.mark.live
@pytest.mark.parametrize("vendor", ["Microsoft", "Okta", "Salesforce"])
def test_live_fedramp_authorized_vendors(monkeypatch, vendor):
    monkeypatch.setattr(fedramp_connector, "_fedramp_cache", None)
    assert fedramp_connector.check_fedramp(vendor)["fedramp_tier"] == "VERIFIED_REGISTRY"
