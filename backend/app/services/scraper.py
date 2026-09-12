import re
import urllib.parse
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime

# ── Industry classification ────────────────────────────────────────────────

# Maps yfinance's freeform industry strings to the standard taxonomy
# the RAG layer uses for benchmark retrieval.
_YFINANCE_INDUSTRY_MAP = {
    "information technology services": "IT Services",
    "it services":                     "IT Services",
    "computer services":               "IT Services",
    "business process outsourcing":    "IT Services",
    "technology services":             "IT Services",
    "consulting":                      "IT Services",
    # "software—application":            "Software",
    "software\u2014application":        "Software",
    "software - application":           "Software",
    "software application":            "Software",
    "software infrastructure":         "Software",
    "cloud computing":                 "Software",
    "internet content & information":  "Software",
    "internet security":               "Cybersecurity",
    "cybersecurity":                   "Cybersecurity",
    "financial services":              "Financial Services",
    "banks":                           "Financial Services",
    "insurance":                       "Financial Services",
    "asset management":                "Financial Services",
    "capital markets":                 "Financial Services",
    "healthcare":                      "Healthcare",
    "biotechnology":                   "Healthcare",
    "pharmaceutical":                  "Healthcare",
    "medical devices":                 "Healthcare",
    "health information services":     "Healthcare",
    "manufacturing":                   "Manufacturing",
    "industrial":                      "Manufacturing",
    "aerospace":                       "Manufacturing",
    "automotive":                      "Manufacturing",
    "electronic components":           "Manufacturing",
    "logistics":                       "Logistics",
    "freight":                         "Logistics",
    "shipping":                        "Logistics",
    "transportation":                  "Logistics",
    "supply chain":                    "Logistics",
    "telecom services":                "Telecommunications",
    "communication equipment":         "Telecommunications",
}

_INDUSTRY_KEYWORD_SIGNALS = {
    "IT Services":        ["it services", "technology services", "outsourcing", "bpo",
                           "it consulting", "it solutions", "managed services",
                           "digital transformation"],
    "Software":           ["software platform", "saas", "cloud platform",
                           "enterprise software", "data platform", "workflow automation",
                           "no-code", "low-code", "subscription software"],
    "Cybersecurity":      ["cybersecurity", "cyber security", "security software",
                           "threat detection", "soc 2", "compliance automation",
                           "grc", "vulnerability management", "endpoint security",
                           "compliance platform", "security compliance",
                           "audit automation", "continuous compliance",
                           "information security"],
    "Financial Services": ["financial services", "banking", "insurance", "fintech",
                           "payments", "lending", "wealth management", "trading"],
    "Healthcare":         ["healthcare", "pharma", "medical", "health", "biotech",
                           "clinical", "life sciences", "patient"],
    "Manufacturing":      ["manufacturing", "industrial", "factory", "production",
                           "hardware", "semiconductor", "electronics"],
    "Logistics":          ["logistics", "supply chain", "shipping", "freight",
                           "warehouse", "last mile", "fleet"],
    "Telecommunications": ["telecom", "wireless", "network", "broadband", "carrier",
                           "communications provider"],
    "Government":         ["government", "federal", "public sector", "defense",
                           "agency", "municipal"],
}


def _normalize_yfinance_industry(yf_industry: str) -> str:
    """
    Map yfinance's freeform industry strings (e.g. 'Software—Application')
    to the standard taxonomy used by the RAG benchmark retrieval layer.
    Returns the original string unchanged if no mapping is found — still
    useful context for agents even if not in the standard taxonomy.
    """
    if not yf_industry:
        return yf_industry
    lower = yf_industry.lower()
    for keyword, mapped in _YFINANCE_INDUSTRY_MAP.items():
        if keyword in lower:
            return mapped
    return yf_industry


def _classify_industry_fallback(company_name: str) -> str | None:
    """
    For private companies where yfinance returns no industry, classify
    via DDGS search snippets + keyword scoring.

    Deliberately avoids an LLM call — keyword matching is reliable enough
    for industry classification and doesn't burn daily token budget.
    Returns None if classification is genuinely ambiguous (score = 0).
    """
    try:
        from ddgs import DDGS
        results = DDGS().text(
            f'"{company_name}" software company what industry sector',
            max_results=8
        )
        if not results:
            return None

        combined = " ".join(
            (r.get("title", "") + " " + r.get("body", "")).lower()
            for r in results
        )

        scores = {
            industry: sum(1 for kw in keywords if kw in combined)
            for industry, keywords in _INDUSTRY_KEYWORD_SIGNALS.items()
        }

        best = max(scores, key=scores.get)
        if scores[best] > 0:
            print(f"  ✅ Industry classified (search fallback): {best} "
                  f"(score={scores[best]})")
            return best

        return None

    except Exception as e:
        print(f"  → Industry classification fallback failed: {e}")
        return None

# ══════════════════════════════════════════════════════════
# PUBLIC FUNCTIONS
# ══════════════════════════════════════════════════════════

def scrape_vendor_financials(company_name: str) -> dict:
    print(f"\n  → Scraping data for: {company_name}")
    data    = {}
    sources = {}

    yf_data = _scrape_yfinance(company_name)
    if yf_data:
        data.update(yf_data)
        for key in yf_data:
            sources[key] = "Yahoo Finance / yfinance"
        # Normalize yfinance's freeform industry string to standard taxonomy
        if data.get("industry"):
            raw = data["industry"]
            data["industry"] = _normalize_yfinance_industry(raw)
            if data["industry"] != raw:
                print(f"  ✅ Industry normalized: '{raw}' → '{data['industry']}'")

    years = _scrape_years_from_wikipedia(company_name)
    if years:
        data["years_in_operation"]    = years
        sources["years_in_operation"] = "Wikipedia"
        print(f"  ✅ Years in operation from Wikipedia: {years}")

    revenue = _scrape_revenue_macrotrends(company_name)
    if revenue:
        data.update(revenue)
        for key in revenue:
            sources[key] = "Macrotrends"
        print(f"  ✅ Revenue history from Macrotrends: {list(revenue.keys())}")

    credit = _scrape_credit_yahoo(company_name)
    if credit:
        data.update(credit)
        for key in credit:
            sources[key] = "Yahoo Finance"
        print(f"  ✅ Credit info from Yahoo Finance: {credit}")

    # Fallback: classify industry for private companies where yfinance
    # returned no industry. Without this, routes.py falls back to the
    # CLIENT's industry for the vendor — which is wrong.
    if not data.get("industry"):
        classified = _classify_industry_fallback(company_name)
        if classified:
            data["industry"] = classified
            sources["industry"] = "search-based classification (private company)"

    data["_sources"] = sources
    print(f"\n  → Final scraped fields: {list(data.keys())}")
    return data


def scrape_vendor_compliance(company_name: str, industry: str = None) -> dict:
    print(f"\n  → Scraping compliance data for: {company_name}")
    data = {}

    if industry:
        data["industry"] = industry

    data["ofac_clear"] = _check_ofac(company_name)
    # _scrape_certifications removed — cert detection now handled by
    # compliance_discovery.py (EDGAR + CSA STAR + FedRAMP + web fallback)
    # which routes.py calls separately and merges into compliance_data.
    data.update(_scrape_legal_news(company_name))
    data["recent_news"] = scrape_vendor_news(company_name)

    print(f"  → Compliance fields: {list(data.keys())}")
    return data


def scrape_vendor_news(company_name: str, max_articles: int = 5) -> list:
    print(f"\n  → Scraping news for: {company_name}")

    try:
        query = company_name.replace(" ", "%20")
        url   = (
            f"https://news.google.com/rss/search?"
            f"q={query}&hl=en-US&gl=US&ceid=US:en"
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=8)

        if resp.status_code != 200:
            return _news_fallback(company_name, max_articles)

        soup  = BeautifulSoup(resp.content, "lxml-xml")
        items = soup.find_all("item")[:max_articles]

        if not items:
            return _news_fallback(company_name, max_articles)

        articles = []
        for item in items:
            title   = item.find("title")
            desc    = item.find("description")
            source  = item.find("source")
            pubdate = item.find("pubDate")
            articles.append({
                "title":   title.get_text(strip=True)  if title   else "",
                "snippet": BeautifulSoup(
                               desc.get_text(strip=True), "html.parser"
                           ).get_text() if desc else "",
                "source":  source.get_text(strip=True) if source  else "",
                "date":    pubdate.get_text(strip=True) if pubdate else "",
            })

        print(f"  ✅ Found {len(articles)} news articles")
        return articles

    except Exception as e:
        print(f"  → RSS error: {e}, trying fallback...")
        return _news_fallback(company_name, max_articles)


# ══════════════════════════════════════════════════════════
# FINANCIAL SCRAPERS
# ══════════════════════════════════════════════════════════

def _scrape_yfinance(company_name: str) -> dict:
    try:
        ticker_symbol = _find_ticker(company_name)
        if not ticker_symbol:
            return {}

        print(f"  ✅ Ticker found: {ticker_symbol} (yfinance)")
        ticker = yf.Ticker(ticker_symbol)
        info   = ticker.info

        if not info:
            return {}

        # Validate ticker actually belongs to this company.
        # Prevents "Vanta" → 0220.KL (Malaysian oil company).
        long_name = (info.get("longName") or info.get("shortName") or "").lower()
        name_tokens = set(re.findall(r"[a-z]{3,}", company_name.lower()))
        name_tokens -= {"the", "inc", "ltd", "corp", "group", "company"}
        long_name_tokens = set(re.findall(r"[a-z]{3,}", long_name))
        if long_name and name_tokens and not (name_tokens & long_name_tokens):
            print(f"  → Ticker mismatch: '{info.get('longName')}' ≠ '{company_name}' — skipping yfinance")
            return {}

        result = {
            "company_name":         info.get("longName", company_name),
            "industry":             info.get("industry"),
            "current_ratio":        info.get("currentRatio"),
            "quick_ratio":          info.get("quickRatio"),
            "debt_equity_ratio":    _safe_divide(info.get("debtToEquity"), 100),
            "ebitda":               info.get("ebitda"),
            "net_margin":           round(info.get("profitMargins", 0) * 100, 2) if info.get("profitMargins") else None,
            "gross_margin":         round(info.get("grossMargins", 0) * 100, 2) if info.get("grossMargins") else None,
            "revenue_growth":       round(info.get("revenueGrowth", 0) * 100, 2) if info.get("revenueGrowth") else None,
            "credit_rating":        info.get("creditRating", "N/A"),
            "customer_count":       None,
            "top_customer_revenue": None,
            "duns_credit_score":    None,
        }

        # Flag non-USD currencies for non-US tickers
        if ticker_symbol and ("." in ticker_symbol):
            exchange = ticker_symbol.split(".")[-1]
            currency_map = {"NS": "INR", "BO": "INR", "L": "GBP", "PA": "EUR", "DE": "EUR"}
            currency = currency_map.get(exchange)
            if currency and currency != "USD":
                result["currency"] = currency
                result["currency_note"] = f"Financial figures in {currency}, not USD"

        try:
            financials = ticker.financials
            if financials is not None and not financials.empty:
                if "Total Revenue" in financials.index:
                    for date, val in financials.loc["Total Revenue"].items():
                        if val and str(val) != "nan":
                            result[f"revenue_{date.year}"] = int(val)
        except Exception:
            pass

        try:
            ebit     = ticker.financials.loc["EBIT"].iloc[0] \
                       if "EBIT" in ticker.financials.index else None
            interest = ticker.financials.loc["Interest Expense"].iloc[0] \
                       if "Interest Expense" in ticker.financials.index else None
            if ebit and interest and interest != 0:
                result["interest_coverage"] = round(abs(ebit / interest), 2)
        except Exception:
            pass

        return {k: v for k, v in result.items() if v is not None}

    except Exception as e:
        print(f"  → yfinance error: {e}")
        return {}


def _scrape_years_from_wikipedia(company_name: str) -> int:
    try:
        slug    = company_name.replace(" ", "_")
        url     = f"https://en.wikipedia.org/wiki/{slug}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp    = requests.get(url, headers=headers, timeout=6)

        if resp.status_code != 200:
            return None

        soup    = BeautifulSoup(resp.text, "html.parser")
        infobox = soup.find("table", {"class": "infobox"})

        if not infobox:
            return None

        for row in infobox.find_all("tr"):
            header = row.find("th")
            cell   = row.find("td")
            if header and cell:
                if "founded" in header.get_text(strip=True).lower() or \
                   "inception" in header.get_text(strip=True).lower():
                    match = re.search(
                        r"\b(1[89]\d{2}|20[0-2]\d)\b",
                        cell.get_text(strip=True)
                    )
                    if match:
                        return datetime.now().year - int(match.group(1))
        return None

    except Exception as e:
        print(f"  → Wikipedia scrape error: {e}")
        return None


def _scrape_revenue_macrotrends(company_name: str) -> dict:
    try:
        ticker_symbol = _find_ticker(company_name)
        if not ticker_symbol:
            return {}

        slug = company_name.lower().replace(" ", "-")
        url  = (
            f"https://www.macrotrends.net/stocks/charts/"
            f"{ticker_symbol}/{slug}/revenue"
        )
        headers = {"User-Agent": "Mozilla/5.0"}
        resp    = requests.get(url, headers=headers, timeout=8)

        if resp.status_code != 200:
            return {}

        soup = BeautifulSoup(resp.text, "html.parser")
        data = {}

        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cols = row.find_all("td")
                if len(cols) >= 2:
                    year_match = re.search(
                        r"(20\d{2})", cols[0].get_text(strip=True)
                    )
                    if year_match:
                        val = _parse_dollar(cols[1].get_text(strip=True))
                        if val:
                            data[f"revenue_{year_match.group(1)}"] = val
        return data

    except Exception as e:
        print(f"  → Macrotrends error: {e}")
        return {}


def _scrape_credit_yahoo(company_name: str) -> dict:
    try:
        ticker_symbol = _find_ticker(company_name)
        if not ticker_symbol:
            return {}

        url     = f"https://finance.yahoo.com/quote/{ticker_symbol}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp    = requests.get(url, headers=headers, timeout=6)

        if resp.status_code != 200:
            return {}

        text  = BeautifulSoup(resp.text, "html.parser").get_text()
        match = re.search(
            r"(AAA|AA\+|AA|AA-|A\+|A|A-|BBB\+|BBB|BBB-|BB\+|BB|BB-|B\+|B|CCC)",
            text
        )
        return {"credit_rating": match.group(1)} if match else {}

    except Exception as e:
        print(f"  → Yahoo Finance error: {e}")
        return {}


# ══════════════════════════════════════════════════════════
# COMPLIANCE SCRAPERS
# ══════════════════════════════════════════════════════════

def _check_ofac(company_name: str) -> bool:
    try:
        resp = requests.get(
            "https://sanctionslistservice.ofac.treas.gov/"
            "api/PublicationPreview/resources/SDN.JSON",
            timeout=5
        )
        if resp.status_code == 200:
            if company_name.lower() in resp.text.lower():
                print(f"  ⚠️  OFAC: Possible match for {company_name}")
                return False
        return True
    except Exception:
        return True


def _scrape_certifications(company_name: str) -> dict:
    certs_found   = []
    certs_sources = {}

    cert_map = {
        "iso/iec 27001": "ISO27001",
        "iso 27001":     "ISO27001",
        "iso27001":      "ISO27001",
        "iso 9001":      "ISO9001",
        "iso 14001":     "ISO14001",
        "iso 45001":     "ISO45001",
        "iso 27701":     "ISO27701",
        "iso 22301":     "ISO22301",
        "soc 2":         "SOC2_Type2",
        "soc2":          "SOC2_Type2",
        "soc 1":         "SOC1",
        "gdpr":          "GDPR",
        "pci dss":       "PCI_DSS",
        "pci-dss":       "PCI_DSS",
        "hipaa":         "HIPAA",
        "fedramp":       "FedRAMP",
        "ccpa":          "CCPA",
        "csa star":      "CSA_STAR",
    }

    # ── Source 1: Certipedia ──────────────────────────────
    try:
        url  = f"https://www.certipedia.com/search?q={company_name.replace(' ', '+')}"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        text = resp.text.lower()
        for keyword, cert in cert_map.items():
            if keyword in text and cert not in certs_found:
                certs_found.append(cert)
                certs_sources[cert] = "Certipedia"
                print(f"  ✅ {cert} found on Certipedia")
    except Exception as e:
        print(f"  → Certipedia error: {e}")

    # ── Source 2: DuckDuckGo → company's own compliance pages ──
    compliance_urls = _find_compliance_urls(company_name)
    print(f"  → Checking {len(compliance_urls)} compliance pages...")

    for url in compliance_urls:
        try:
            resp = requests.get(
                url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6
            )
            if resp.status_code != 200:
                continue
            text      = resp.text.lower()
            found_here = []
            for keyword, cert in cert_map.items():
                if keyword in text and cert not in certs_found:
                    certs_found.append(cert)
                    certs_sources[cert] = url
                    found_here.append(cert)
            if found_here:
                print(f"  ✅ Found {found_here} at {url}")
        except Exception:
            continue

    # ── Source 3: BSI ─────────────────────────────────────
    try:
        url  = (
            f"https://www.bsigroup.com/en-GB/validate-bsi-issued-certificates/"
            f"?q={company_name.replace(' ', '%20')}"
        )
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        text = resp.text.lower()
        for keyword, cert in cert_map.items():
            if keyword in text and cert not in certs_found:
                certs_found.append(cert)
                certs_sources[cert] = "BSI Group"
                print(f"  ✅ {cert} found on BSI")
    except Exception as e:
        print(f"  → BSI error: {e}")

    print(f"  → Total certifications found: {certs_found}")
    return {
        "certifications_found":   certs_found,
        "certifications_sources": certs_sources,
    }


def _find_compliance_urls(company_name: str) -> list:
    """
    Uses DuckDuckGo to dynamically find any company's
    compliance/trust pages. No hardcoding — works for any vendor.
    """
    urls = []
    company_domain = company_name.lower().split()[0]

    try:
        queries = [
            f"{company_name} ISO27001 SOC2 GDPR compliance certifications",
            f"{company_name} trust center security compliance",
        ]
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        compliance_keywords = [
            "trust", "compliance", "security",
            "certif", "gdpr", "privacy", "iso"
        ]

        for query in queries:
            resp = requests.post(
                f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}",
                headers=headers,
                timeout=8
            )
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            # Extract from result__url tags
            for a in soup.find_all("a", class_="result__url"):
                href = a.get("href", "").strip()
                if not href.startswith("http"):
                    href = "https://" + href
                if (
                    any(k in href.lower() for k in compliance_keywords)
                    and company_domain in href.lower()
                    and href not in urls
                ):
                    urls.append(href)
                    print(f"  → Found: {href}")

            # Extract from result__a tags (unwrap DuckDuckGo redirects)
            for a in soup.find_all("a", class_="result__a"):
                href = a.get("href", "")
                if "uddg=" in href:
                    params = urllib.parse.parse_qs(
                        urllib.parse.urlparse(href).query
                    )
                    real = params.get("uddg", [""])[0]
                    if real:
                        href = urllib.parse.unquote(real)
                if (
                    href.startswith("http")
                    and any(k in href.lower() for k in compliance_keywords)
                    and company_domain in href.lower()
                    and href not in urls
                ):
                    urls.append(href)
                    print(f"  → Found: {href}")

            if len(urls) >= 3:
                break

    except Exception as e:
        print(f"  → DuckDuckGo error: {e}")

    return urls[:5]


def _scrape_legal_news(company_name: str) -> dict:
    try:
        query   = f"{company_name} lawsuit fine SEC violation 2023 2024"
        url     = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp    = requests.get(url, headers=headers, timeout=5)
        text    = resp.text.lower()

        red_flags = []
        signals = {
            f"{company_name.lower()} sec violation":    "SEC regulatory violation detected in news",
            f"{company_name.lower()} class action":     "Class action lawsuit mentioned in news",
            f"{company_name.lower()} data breach":      "Data breach mentioned in news",
            f"{company_name.lower()} criminal charges": "Criminal charges mentioned in news",
            f"{company_name.lower()} ftc":              "FTC regulatory action detected in news",
        }
        for keyword, flag in signals.items():
            if keyword in text:
                red_flags.append(flag)

        return {"news_red_flags": red_flags} if red_flags else {}

    except Exception:
        return {}


def _news_fallback(company_name: str, max_articles: int = 5) -> list:
    try:
        query = company_name.replace(" ", "+")
        url   = f"https://www.bing.com/news/search?q={query}&format=RSS"
        resp  = requests.get(
            url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8
        )
        soup     = BeautifulSoup(resp.content, "lxml-xml")
        articles = []

        for item in soup.find_all("item")[:max_articles]:
            title = item.find("title")
            desc  = item.find("description")
            if title:
                articles.append({
                    "title":   title.get_text(strip=True),
                    "snippet": BeautifulSoup(
                                   desc.get_text(strip=True), "html.parser"
                               ).get_text() if desc else "",
                    "source":  "Bing News",
                    "date":    "",
                })

        print(f"  ✅ Fallback found {len(articles)} articles (Bing News)")
        return articles

    except Exception as e:
        print(f"  → Fallback also failed: {e}")
        return []


# ══════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════

def _find_ticker(company_name: str) -> str:
    try:
        url  = (
            f"https://query2.finance.yahoo.com/v1/finance/"
            f"search?q={company_name.replace(' ', '+')}&quotesCount=1"
        )
        resp   = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        quotes = resp.json().get("quotes", [])
        return quotes[0].get("symbol", "") if quotes else ""
    except Exception:
        return ""


def _parse_dollar(text: str) -> float:
    text = text.replace("$", "").replace(",", "").strip()
    try:
        if "B" in text:
            return float(text.replace("B", "")) * 1_000_000_000
        elif "M" in text:
            return float(text.replace("M", "")) * 1_000_000
        elif "K" in text:
            return float(text.replace("K", "")) * 1_000
        return float(text)
    except Exception:
        return None


def _safe_divide(val, divisor):
    try:
        return round(val / divisor, 4) if val else None
    except Exception:
        return None