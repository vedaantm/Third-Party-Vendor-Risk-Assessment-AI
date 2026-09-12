# backend/app/services/cert_pipeline.py

"""
Hybrid certification discovery pipeline.

Fix 1: Domain validation — validates resolved domain belongs to the company.
Fix 4: PDF size cap — skips PDFs over 5MB.
"""

import urllib.parse
import re
from io import BytesIO

import requests
import yfinance as yf
from bs4 import BeautifulSoup
from pypdf import PdfReader
from ddgs import DDGS

from app.services.cert_extraction_agent import extract_certifications
from app.services.scraper import _find_ticker

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.google.com/",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Connection": "keep-alive",
}

_session = requests.Session()
_session.headers.update(HEADERS)

PDF_SIZE_LIMIT_BYTES = 5 * 1024 * 1024  # 5MB

SITE_QUERIES_TEMPLATE = [
    "site:{domain} ISO27001 SOC2 GDPR certifications",
    "site:{domain} trust compliance security",
    "site:{domain} ESG governance compliance certifications",
]

FALLBACK_QUERIES_TEMPLATE = [
    "{company} trust center security compliance",
    "{company} ISO27001 SOC2 certifications",
    "{company} ESG governance compliance certifications",
]

MAX_PAGES_TO_FETCH = 6 #previously 8

DOMAIN_STOPWORDS = {
    "tech", "corp", "group", "company", "services",
    "solutions", "the", "inc", "ltd",
}


def _get_company_domain(company_name: str) -> str | None:
    """
    FIX 1: Validates the resolved domain actually belongs to this company.
    Checks yfinance longName matches company name, and domain root contains
    a meaningful company name token. Prevents Vanta → ovbhd.com etc.
    """
    try:
        ticker_symbol = _find_ticker(company_name)
        if not ticker_symbol:
            return None

        info = yf.Ticker(ticker_symbol).info

        long_name = info.get("longName", "") or info.get("shortName", "")
        if long_name:
            name_tokens = set(re.findall(r"[a-z]+", company_name.lower()))
            name_tokens -= DOMAIN_STOPWORDS
            long_tokens = set(re.findall(r"[a-z]+", long_name.lower()))
            long_tokens -= DOMAIN_STOPWORDS
            if not (name_tokens & long_tokens):
                print(f"  → yfinance name mismatch: '{long_name}' ≠ '{company_name}' — domain rejected")
                return None

        website = info.get("website")
        if not website:
            return None

        netloc = urllib.parse.urlparse(website).netloc.lower()
        domain = netloc[4:] if netloc.startswith("www.") else netloc
        domain_root = domain.split(".")[0]

        meaningful_tokens = {
            t for t in re.findall(r"[a-z]{4,}", company_name.lower())
            if t not in DOMAIN_STOPWORDS
        }
        if meaningful_tokens and not any(t in domain_root for t in meaningful_tokens):
            print(f"  → Domain '{domain}' doesn't match '{company_name}' — domain rejected")
            return None

        return domain

    except Exception as e:
        print(f"  → domain lookup failed: {e}")
        return None


def _domain_matches(href: str, domain: str) -> bool:
    netloc = urllib.parse.urlparse(href).netloc.lower()
    netloc = netloc[4:] if netloc.startswith("www.") else netloc
    return netloc == domain or netloc.endswith("." + domain)


def _search_web(query: str) -> list[str]:
    try:
        results = DDGS().text(query, max_results=10)
        return [r.get("href", "") for r in results if r.get("href", "").startswith("http")]
    except Exception as e:
        print(f"  → search failed for '{query}': {e}")
        return []


def _extract_pdf_text(content: bytes) -> str:
    reader = PdfReader(BytesIO(content))
    return " ".join(
        " ".join((page.extract_text() or "").split())
        for page in reader.pages
    )


def _fetch_page_text(url: str) -> str:
    try:
        resp = _session.get(url, timeout=15)

        if resp.status_code != 200:
            print(f"  → skipped (status {resp.status_code}): {url}")
            return ""

        content_type = resp.headers.get("Content-Type", "")
        is_pdf = "application/pdf" in content_type or url.lower().endswith(".pdf")

        if is_pdf:
            # FIX 4: Skip oversized PDFs before parsing
            content_length = int(resp.headers.get("Content-Length", 0))
            if content_length > PDF_SIZE_LIMIT_BYTES:
                print(f"  → skipped PDF (too large: {content_length/1024/1024:.1f}MB): {url}")
                return ""
            if len(resp.content) > PDF_SIZE_LIMIT_BYTES:
                print(f"  → skipped PDF (too large after fetch): {url}")
                return ""
            try:
                return _extract_pdf_text(resp.content)
            except Exception as e:
                print(f"  → PDF parse failed for {url}: {type(e).__name__}: {e}")
                return ""

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        return " ".join(soup.get_text(separator=" ").split())

    except Exception as e:
        print(f"  → fetch failed for {url}: {type(e).__name__}: {e}")
        return ""


async def discover_and_extract_certifications(company_name: str) -> dict:
    domain = _get_company_domain(company_name)
    fallback_token = company_name.lower().split()[0]
    candidate_urls = []

    if domain:
        print(f"  → Resolved domain: {domain}")
        templates = SITE_QUERIES_TEMPLATE
    else:
        print("  → Could not resolve domain, falling back to keyword search")
        templates = FALLBACK_QUERIES_TEMPLATE

    for template in templates:
        query = template.format(domain=domain, company=company_name)
        for url in _search_web(query):
            if domain:
                is_match = _domain_matches(url, domain)
            else:
                is_match = fallback_token in url.lower()
            if is_match and url not in candidate_urls:
                candidate_urls.append(url)

    candidate_urls = candidate_urls[:MAX_PAGES_TO_FETCH]
    print(f"  → Found {len(candidate_urls)} candidate pages for {company_name}")

    pages = []
    for url in candidate_urls:
        text = _fetch_page_text(url)
        if text:
            pages.append({"url": url, "text": text})
            print(f"  → Fetched: {url} ({len(text)} chars)")

    if not pages:
        return {
            "certifications_found": [],
            "evidence": [],
            "regulatory_frameworks_disclosed": [],
            "uncertain": ["No pages could be fetched"],
            "explicitly_not_mentioned": [],
            "sources_checked": candidate_urls,
        }

    result = await extract_certifications(pages)
    result["sources_checked"] = candidate_urls
    return result