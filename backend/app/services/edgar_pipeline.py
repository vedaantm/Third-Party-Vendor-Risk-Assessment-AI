# backend/app/services/edgar_pipeline.py

"""
SEC EDGAR-based certification discovery.

Fix 1: Entity validation — validates SEC-registered company name against
the search term before trusting the filing. Prevents ticker collisions
(e.g. "IBM" resolving to IBM Credit LLC instead of IBM Corp).

Fix 2: Regulatory filter — GDPR, CCPA, SOX, FCPA moved to
regulatory_frameworks_disclosed, never certifications_found.
"""

import re

import requests
from bs4 import BeautifulSoup

from app.services.scraper import _find_ticker

EDGAR_HEADERS = {"User-Agent": "VendorRiskPlatform research-contact@example.com"}

ANNUAL_FILING_FORMS = ("10-K", "20-F")

SEARCH_TERMS = [
    "ISO 27001", "ISO/IEC 27001", "ISO27001",
    "ISO 27701", "ISO/IEC 27701", "ISO27701",
    "ISO 27017", "ISO/IEC 27017",
    "ISO 27018", "ISO/IEC 27018",
    "ISO 9001", "ISO9001",
    "ISO 14001", "ISO14001",
    "ISO 45001", "ISO45001",
    "ISO 13485", "ISO13485",
    "ISO 22301", "ISO22301",
    "ISO 20000", "ISO20000",
    "ISO 42001", "ISO42001",
    "SOC 1", "SOC1", "SOC Type I",
    "SOC 2", "SOC2", "SOC Type II",
    "SOC 3", "SOC3",
    "SSAE 18", "ISAE 3402",
    "GDPR", "CCPA", "HIPAA",
    "PCI DSS", "PCI-DSS",
    "FedRAMP",
    "NIST CSF", "NIST Cybersecurity Framework",
    "CSA STAR",
    "SOX", "Sarbanes-Oxley",
    "FCPA",
]

REGULATORY_ONLY = {"GDPR", "CCPA", "SOX", "FCPA"}

WINDOW_RADIUS = 750
MAX_HITS_PER_TERM = 2
MAX_TOTAL_CHARS = 8000 #previously 12000

_ticker_cik_cache: dict | None = None


def _load_ticker_cik_map() -> dict:
    global _ticker_cik_cache
    if _ticker_cik_cache is not None:
        return _ticker_cik_cache
    try:
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=EDGAR_HEADERS, timeout=15,
        )
        data = resp.json()
        _ticker_cik_cache = {
            entry["ticker"].upper(): {
                "cik": entry["cik_str"],
                "title": entry["title"],
            }
            for entry in data.values()
        }
    except Exception as e:
        print(f"  → EDGAR ticker map load failed: {e}")
        _ticker_cik_cache = {}
    return _ticker_cik_cache


def _names_plausibly_match(name_a: str, name_b: str) -> bool:
    stopwords = {
        "ltd", "limited", "inc", "incorporated", "corp", "corporation",
        "co", "company", "technologies", "technology", "group", "plc",
        "llc", "holdings", "the", "se", "ag", "nv", "bv",
    }
    tokens_a = set(re.findall(r"[a-z]+", name_a.lower())) - stopwords
    tokens_b = set(re.findall(r"[a-z]+", name_b.lower())) - stopwords
    return bool(tokens_a & tokens_b)


def _resolve_cik_by_name(company_name: str) -> str | None:
    try:
        resp = requests.get(
            "https://www.sec.gov/cgi-bin/browse-edgar",
            params={
                "action": "getcompany",
                "company": company_name,
                "dateb": "", "owner": "include", "count": "10",
            },
            headers=EDGAR_HEADERS, timeout=15,
        )
        match = re.search(r"CIK=(\d{10})", resp.text)
        return match.group(1) if match else None
    except Exception as e:
        print(f"  → EDGAR name search failed: {e}")
        return None


def _fetch_and_validate_submissions(cik: str, company_name: str) -> tuple[str | None, dict | None]:
    """
    FIX 1: Fetches EDGAR submissions JSON for a CIK and validates that the
    SEC-registered name actually matches the company we searched for.
    Returns (registered_name, latest_filing) or (None, None) on mismatch.
    """
    try:
        resp = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=EDGAR_HEADERS, timeout=15,
        )
        data = resp.json()
        registered_name = data.get("name", "")

        if not _names_plausibly_match(company_name, registered_name):
            print(f"  → CIK {cik} name mismatch: '{registered_name}' ≠ '{company_name}' — rejecting")
            return None, None

        print(f"  → CIK {cik} validated: '{registered_name}'")

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        docs = recent.get("primaryDocument", [])

        for form, date, accession, doc in zip(forms, dates, accessions, docs):
            if form in ANNUAL_FILING_FORMS:
                accession_clean = accession.replace("-", "")
                url = (
                    f"https://www.sec.gov/Archives/edgar/data/"
                    f"{int(cik)}/{accession_clean}/{doc}"
                )
                return registered_name, {"url": url, "form": form, "filing_date": date}

        return registered_name, None

    except Exception as e:
        print(f"  → EDGAR submissions fetch failed: {e}")
        return None, None


def _resolve_cik(
    company_name: str, ticker_symbol: str | None = None
) -> tuple[str | None, str | None, dict | None]:
    """
    Returns (cik, registered_name, latest_annual_filing) or (None, None, None).
    Validates name at every step — never trusts a CIK without confirming
    the registered name matches the company we searched for.
    """
    ticker_symbol = ticker_symbol or _find_ticker(company_name)
    candidate_cik = None

    if ticker_symbol:
        bare_ticker = ticker_symbol.split(".")[0].upper()
        ticker_map = _load_ticker_cik_map()
        entry = ticker_map.get(bare_ticker)
        if entry and _names_plausibly_match(company_name, entry["title"]):
            candidate_cik = str(entry["cik"]).zfill(10)

    if candidate_cik:
        name, filing = _fetch_and_validate_submissions(candidate_cik, company_name)
        if name:
            return candidate_cik, name, filing
        print(f"  → Ticker CIK failed name validation, trying name search")

    name_cik = _resolve_cik_by_name(company_name)
    if name_cik and name_cik != candidate_cik:
        name, filing = _fetch_and_validate_submissions(name_cik, company_name)
        if name:
            return name_cik, name, filing

    return None, None, None


def _fetch_filing_text(url: str) -> str:
    resp = requests.get(url, headers=EDGAR_HEADERS, timeout=30)
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())


def _extract_relevant_windows(text: str) -> str:
    windows = []
    for term in SEARCH_TERMS:
        hits = list(re.finditer(re.escape(term), text, re.IGNORECASE))
        for m in hits[:MAX_HITS_PER_TERM]:
            start = max(0, m.start() - WINDOW_RADIUS)
            end = min(len(text), m.end() + WINDOW_RADIUS)
            windows.append((start, end))

    if not windows:
        return ""

    windows.sort()
    merged = [windows[0]]
    for start, end in windows[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    pieces = []
    total = 0
    for start, end in merged:
        piece = text[start:end]
        if total + len(piece) > MAX_TOTAL_CHARS:
            break
        pieces.append(piece)
        total += len(piece)

    return "\n---\n".join(pieces)


async def discover_certifications_from_edgar(
    company_name: str, ticker_symbol: str | None = None
) -> dict:
    from app.services.cert_extraction_agent import extract_certifications

    empty = {
        "certifications_found": [], "evidence": [],
        "regulatory_frameworks_disclosed": [],
        "uncertain": [], "explicitly_not_mentioned": [],
        "sources_checked": [],
        "edgar_cik": None, "edgar_filing_url": None,
    }

    cik, registered_name, filing = _resolve_cik(company_name, ticker_symbol)

    if not cik:
        print(f"  → No validated SEC CIK found for '{company_name}'")
        return empty

    print(f"  → Resolved CIK: {cik} ({registered_name})")

    if not filing:
        print(f"  → No 10-K/20-F found for CIK {cik}")
        return {**empty, "edgar_cik": cik}

    print(f"  → Using {filing['form']} filed {filing['filing_date']}: {filing['url']}")
    full_text = _fetch_filing_text(filing["url"])
    relevant_text = _extract_relevant_windows(full_text)

    if not relevant_text:
        print("  → Filing had no recognizable compliance-term mentions")
        return {
            **empty,
            "sources_checked": [filing["url"]],
            "edgar_cik": cik,
            "edgar_filing_url": filing["url"],
        }

    result = await extract_certifications([{"url": filing["url"], "text": relevant_text}])

    # FIX 2: Belt-and-suspenders regulatory filter at EDGAR layer
    leaked = [c for c in result.get("certifications_found", []) if c in REGULATORY_ONLY]
    if leaked:
        result["certifications_found"] = [
            c for c in result["certifications_found"] if c not in REGULATORY_ONLY
        ]
        existing = set(result.get("regulatory_frameworks_disclosed", []))
        result["regulatory_frameworks_disclosed"] = list(existing | set(leaked))

    result["sources_checked"] = [filing["url"]]
    result["edgar_cik"] = cik
    result["edgar_filing_url"] = filing["url"]
    return result