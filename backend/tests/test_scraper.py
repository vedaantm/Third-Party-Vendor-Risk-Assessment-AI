"""
Vendor data collection (app/services/scraper.py).

Every agent scores what the scraper collects, so a wrong industry mapping or
a silently dropped number changes every downstream score. All HTTP, search
and yfinance calls are faked; the autouse network block in conftest.py would
otherwise make each scraper swallow a RuntimeError and return {}, which looks
like "no data" rather than a failure. Tests therefore also assert that the
fake was actually called.
"""

import sys
from datetime import datetime
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest

from app.services import scraper


class FakeResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code


class FakeRequests:
    """Stands in for the `requests` module; routes get() by URL substring."""

    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        for fragment, reply in self.routes.items():
            if fragment in url:
                if isinstance(reply, Exception):
                    raise reply
                return reply
        raise AssertionError(f"unexpected URL in test: {url}")


@pytest.fixture
def fake_http(monkeypatch):
    def install(routes):
        fake = FakeRequests(routes)
        monkeypatch.setattr(scraper, "requests", fake)
        return fake

    return install


# ── Industry normalisation ────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Software—Application", "Software"),
        ("Software - Application", "Software"),
        ("Information Technology Services", "IT Services"),
        ("Banks—Regional", "Financial Services"),
        ("Insurance—Life", "Financial Services"),
        ("Medical Devices", "Healthcare"),
        ("Biotechnology", "Healthcare"),
        ("Aerospace & Defense", "Manufacturing"),
        ("Integrated Freight & Logistics", "Logistics"),
        ("Telecom Services", "Telecommunications"),
    ],
)
def test_normalize_maps_yfinance_industries_to_taxonomy(raw, expected):
    assert scraper._normalize_yfinance_industry(raw) == expected


def test_normalize_passes_unknown_industry_through_unchanged():
    assert scraper._normalize_yfinance_industry("Gold") == "Gold"


@pytest.mark.parametrize("raw", ["", None])
def test_normalize_handles_empty_industry(raw):
    assert scraper._normalize_yfinance_industry(raw) == raw


@pytest.mark.xfail(
    strict=True,
    reason="BUG: the map only has 'software infrastructure' with a space; Yahoo's "
    "'Software—Infrastructure' (Okta, Microsoft, Cloudflare) is left unmapped, "
    "so RAG benchmark retrieval gets no 'Software' match",
)
def test_normalize_maps_software_infrastructure_em_dash():
    assert scraper._normalize_yfinance_industry("Software—Infrastructure") == "Software"


@pytest.mark.xfail(
    strict=True,
    reason="BUG: substring match: 'cred(it services)' hits the 'it services' key, so "
    "Visa, Mastercard and PayPal are classified as IT Services",
)
def test_normalize_does_not_map_credit_services_to_it_services():
    assert (
        scraper._normalize_yfinance_industry("Credit Services") == "Financial Services"
    )


# ── Industry fallback for private companies ───────────────


@pytest.fixture
def fake_ddgs(monkeypatch):
    def install(results=None, error=None):
        queries = []

        class DDGS:
            def text(self, query, max_results=None):
                queries.append(query)
                if error:
                    raise error
                return results

        module = ModuleType("ddgs")
        module.DDGS = DDGS
        monkeypatch.setitem(sys.modules, "ddgs", module)
        return queries

    return install


def test_fallback_classifies_by_keyword_score(fake_ddgs):
    queries = fake_ddgs(
        [
            {
                "title": "Vanta - compliance automation",
                "body": "A SaaS security compliance platform",
            },
            {
                "title": "Vanta raises",
                "body": "continuous compliance and audit automation for SOC 2",
            },
            {"title": "About", "body": "enterprise software"},
        ]
    )
    assert scraper._classify_industry_fallback("Vanta") == "Cybersecurity"
    assert queries and '"Vanta"' in queries[0]


def test_fallback_returns_none_without_any_signal(fake_ddgs):
    fake_ddgs([{"title": "Bakery", "body": "fresh bread daily"}])
    assert scraper._classify_industry_fallback("Crumbs") is None


@pytest.mark.parametrize("results", [[], None])
def test_fallback_returns_none_without_results(fake_ddgs, results):
    fake_ddgs(results)
    assert scraper._classify_industry_fallback("Nobody") is None


def test_fallback_returns_none_when_search_fails(fake_ddgs):
    fake_ddgs(error=RuntimeError("rate limited"))
    assert scraper._classify_industry_fallback("Vanta") is None


# ── yfinance ──────────────────────────────────────────────


def fake_yf(info, financials=None):
    ticker = SimpleNamespace(info=info, financials=financials)
    symbols = []

    def Ticker(symbol):
        symbols.append(symbol)
        return ticker

    return SimpleNamespace(Ticker=Ticker, symbols=symbols)


def install_yf(monkeypatch, info, symbol="OKTA", financials=None):
    yf = fake_yf(info, financials)
    monkeypatch.setattr(scraper, "_find_ticker", lambda name: symbol)
    monkeypatch.setattr(scraper, "yf", yf)
    return yf


OKTA_INFO = {
    "longName": "Okta, Inc.",
    "industry": "Software—Infrastructure",
    "currentRatio": 1.8,
    "quickRatio": 1.6,
    "debtToEquity": 150.0,
    "ebitda": 120_000_000,
    "profitMargins": 0.139,
    "grossMargins": 0.7634,
    "revenueGrowth": 0.12,
}


def test_yfinance_converts_ratios_and_margins(monkeypatch):
    install_yf(monkeypatch, OKTA_INFO)
    data = scraper._scrape_yfinance("Okta")
    assert data["company_name"] == "Okta, Inc."
    assert data["current_ratio"] == 1.8 and data["quick_ratio"] == 1.6
    assert data["debt_equity_ratio"] == 1.5  # Yahoo reports D/E as a percentage
    assert data["net_margin"] == 13.9
    assert data["gross_margin"] == 76.34
    assert data["revenue_growth"] == 12.0
    assert data["ebitda"] == 120_000_000


def test_yfinance_drops_fields_it_cannot_provide(monkeypatch):
    install_yf(monkeypatch, {"longName": "Okta, Inc."})
    data = scraper._scrape_yfinance("Okta")
    assert data == {"company_name": "Okta, Inc."}
    assert None not in data.values()


def test_yfinance_rejects_ticker_of_a_different_company(monkeypatch):
    install_yf(
        monkeypatch,
        {"longName": "Petronas Chemicals Group Berhad", "currentRatio": 2.0},
        symbol="0220.KL",
    )
    assert scraper._scrape_yfinance("Vanta") == {}


def test_yfinance_ignores_corporate_suffixes_when_matching_names(monkeypatch):
    # "Inc" / "Corp" are not evidence the ticker belongs to the vendor
    install_yf(
        monkeypatch, {"longName": "Acme Corp", "currentRatio": 2.0}, symbol="ACME"
    )
    assert scraper._scrape_yfinance("Zenith Inc Corp") == {}


def test_yfinance_without_ticker_does_not_call_yahoo(monkeypatch):
    yf = install_yf(monkeypatch, OKTA_INFO, symbol="")
    assert scraper._scrape_yfinance("Unlisted Startup") == {}
    assert yf.symbols == []


def test_yfinance_empty_info_returns_empty(monkeypatch):
    install_yf(monkeypatch, {})
    assert scraper._scrape_yfinance("Okta") == {}


def test_yfinance_exception_returns_empty(monkeypatch):
    def boom(symbol):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(scraper, "_find_ticker", lambda name: "OKTA")
    monkeypatch.setattr(scraper, "yf", SimpleNamespace(Ticker=boom))
    assert scraper._scrape_yfinance("Okta") == {}


@pytest.mark.parametrize(
    "symbol, currency",
    [
        ("INFY.NS", "INR"),
        ("TCS.BO", "INR"),
        ("SAP.DE", "EUR"),
        ("CAP.PA", "EUR"),
        ("BARC.L", "GBP"),
    ],
)
def test_yfinance_flags_non_usd_listings(monkeypatch, symbol, currency):
    install_yf(
        monkeypatch,
        {"longName": "Example Holdings", "currentRatio": 1.0},
        symbol=symbol,
    )
    data = scraper._scrape_yfinance("Example Holdings")
    assert data["currency"] == currency
    assert currency in data["currency_note"]


@pytest.mark.parametrize("symbol", ["OKTA", "SHOP.TO"])
def test_yfinance_no_currency_note_for_usd_or_unmapped_exchanges(monkeypatch, symbol):
    install_yf(
        monkeypatch, {"longName": "Okta, Inc.", "currentRatio": 1.0}, symbol=symbol
    )
    data = scraper._scrape_yfinance("Okta")
    assert "currency" not in data and "currency_note" not in data


def test_yfinance_reads_revenue_history_and_interest_coverage(monkeypatch):
    financials = pd.DataFrame(
        {
            pd.Timestamp("2025-01-31"): [2_610_000_000.0, 500.0, -50.0],
            pd.Timestamp("2024-01-31"): [2_263_000_000.0, 400.0, -40.0],
            pd.Timestamp("2023-01-31"): [float("nan"), 300.0, -30.0],
        },
        index=["Total Revenue", "EBIT", "Interest Expense"],
    )
    install_yf(monkeypatch, {"longName": "Okta, Inc."}, financials=financials)
    data = scraper._scrape_yfinance("Okta")
    assert data["revenue_2025"] == 2_610_000_000
    assert data["revenue_2024"] == 2_263_000_000
    assert "revenue_2023" not in data  # NaN years are skipped
    assert data["interest_coverage"] == 10.0  # |500 / -50|


def test_yfinance_empty_financials_are_ignored(monkeypatch):
    install_yf(monkeypatch, {"longName": "Okta, Inc."}, financials=pd.DataFrame())
    assert scraper._scrape_yfinance("Okta") == {"company_name": "Okta, Inc."}


@pytest.mark.xfail(
    strict=True,
    reason="BUG: _safe_divide treats 0 as missing, so a debt-free vendor's "
    "debt_equity_ratio is dropped and agents see 'not provided' instead of 0.0",
)
def test_yfinance_keeps_zero_debt_to_equity(monkeypatch):
    install_yf(monkeypatch, {"longName": "Okta, Inc.", "debtToEquity": 0.0})
    assert scraper._scrape_yfinance("Okta")["debt_equity_ratio"] == 0.0


# ── Wikipedia: years in operation ─────────────────────────


def infobox(header, value):
    return f'<table class="infobox"><tr><th>Type</th><td>Public</td></tr><tr><th>{header}</th><td>{value}</td></tr></table>'


@pytest.mark.parametrize("header", ["Founded", "Inception"])
def test_wikipedia_years_from_founded_row(fake_http, header):
    http = fake_http(
        {
            "wikipedia.org": FakeResponse(
                infobox(header, "2009; 17 years ago in San Francisco")
            )
        }
    )
    this_year = datetime.now().year  # noqa: DTZ005 - the scraper uses local time too
    assert scraper._scrape_years_from_wikipedia("Okta Inc") == this_year - 2009
    assert http.urls == ["https://en.wikipedia.org/wiki/Okta_Inc"]


@pytest.mark.parametrize(
    "reply",
    [
        FakeResponse("not found", status_code=404),
        FakeResponse("<html><p>No infobox here</p></html>"),
        FakeResponse(infobox("Founded", "Unknown")),
        FakeResponse(infobox("Headquarters", "1999, San Francisco")),
    ],
)
def test_wikipedia_years_none_when_not_found(fake_http, reply):
    fake_http({"wikipedia.org": reply})
    assert scraper._scrape_years_from_wikipedia("Okta") is None


def test_wikipedia_years_none_on_request_error(fake_http):
    fake_http({"wikipedia.org": RuntimeError("timeout")})
    assert scraper._scrape_years_from_wikipedia("Okta") is None


# ── Macrotrends revenue ───────────────────────────────────

MACROTRENDS_HTML = """
<table>
  <tr><th>Year</th><th>Revenue</th></tr>
  <tr><td>2024</td><td>$2,610</td></tr>
  <tr><td>2023</td><td>$1.5B</td></tr>
  <tr><td>Note</td><td>$9</td></tr>
  <tr><td>2022</td><td>n/a</td></tr>
</table>
"""


def test_macrotrends_parses_revenue_rows(monkeypatch, fake_http):
    monkeypatch.setattr(scraper, "_find_ticker", lambda name: "OKTA")
    http = fake_http({"macrotrends.net": FakeResponse(MACROTRENDS_HTML)})
    data = scraper._scrape_revenue_macrotrends("Okta Inc")
    assert data == {"revenue_2024": 2610.0, "revenue_2023": 1_500_000_000.0}
    assert http.urls == [
        "https://www.macrotrends.net/stocks/charts/OKTA/okta-inc/revenue"
    ]


def test_macrotrends_without_ticker_makes_no_request(monkeypatch, fake_http):
    monkeypatch.setattr(scraper, "_find_ticker", lambda name: "")
    http = fake_http({})
    assert scraper._scrape_revenue_macrotrends("Private Co") == {}
    assert http.urls == []


@pytest.mark.parametrize(
    "reply", [FakeResponse("", status_code=403), RuntimeError("timeout")]
)
def test_macrotrends_failure_returns_empty(monkeypatch, fake_http, reply):
    monkeypatch.setattr(scraper, "_find_ticker", lambda name: "OKTA")
    fake_http({"macrotrends.net": reply})
    assert scraper._scrape_revenue_macrotrends("Okta") == {}


# ── Ticker lookup ─────────────────────────────────────────


class JsonResponse(FakeResponse):
    def __init__(self, payload):
        super().__init__("")
        self._payload = payload

    def json(self):
        return self._payload


def test_find_ticker_returns_first_quote_symbol(fake_http):
    http = fake_http(
        {
            "finance.yahoo.com": JsonResponse(
                {"quotes": [{"symbol": "OKTA"}, {"symbol": "X"}]}
            )
        }
    )
    assert scraper._find_ticker("Okta Inc") == "OKTA"
    assert "q=Okta+Inc" in http.urls[0]


@pytest.mark.parametrize(
    "reply", [JsonResponse({"quotes": []}), JsonResponse({}), RuntimeError("down")]
)
def test_find_ticker_returns_empty_string_when_unknown(fake_http, reply):
    fake_http({"finance.yahoo.com": reply})
    assert scraper._find_ticker("Nobody") == ""


# ── scrape_vendor_financials: merging sources ─────────────


@pytest.fixture
def financial_sources(monkeypatch):
    calls = {"fallback": 0}

    def install(yf=None, years=None, revenue=None, classified=None):
        def fallback(name):
            calls["fallback"] += 1
            return classified

        monkeypatch.setattr(scraper, "_scrape_yfinance", lambda name: dict(yf or {}))
        monkeypatch.setattr(scraper, "_scrape_years_from_wikipedia", lambda name: years)
        monkeypatch.setattr(
            scraper, "_scrape_revenue_macrotrends", lambda name: dict(revenue or {})
        )
        monkeypatch.setattr(scraper, "_classify_industry_fallback", fallback)
        return calls

    return install


def test_financials_merge_all_sources_with_attribution(financial_sources):
    calls = financial_sources(
        yf={"current_ratio": 1.8, "industry": "Software—Application"},
        years=17,
        revenue={"revenue_2024": 2610.0},
    )
    data = scraper.scrape_vendor_financials("Okta")
    assert data["current_ratio"] == 1.8
    assert data["industry"] == "Software"  # normalised
    assert data["years_in_operation"] == 17
    assert data["revenue_2024"] == 2610.0
    assert data["_sources"] == {
        "current_ratio": "Yahoo Finance / yfinance",
        "industry": "Yahoo Finance / yfinance",
        "years_in_operation": "Wikipedia",
        "revenue_2024": "Macrotrends",
    }
    assert calls["fallback"] == 0  # yfinance already gave an industry


def test_financials_classify_private_company_industry(financial_sources):
    calls = financial_sources(classified="Cybersecurity")
    data = scraper.scrape_vendor_financials("Vanta")
    assert calls["fallback"] == 1
    assert data["industry"] == "Cybersecurity"
    assert (
        data["_sources"]["industry"] == "search-based classification (private company)"
    )


def test_financials_with_nothing_found(financial_sources):
    financial_sources()
    data = scraper.scrape_vendor_financials("Nobody")
    assert data == {"_sources": {}}  # no invented industry or numbers


# ── News ──────────────────────────────────────────────────


def rss(*items):
    return "<rss><channel>" + "".join(items) + "</channel></rss>"


GOOGLE_ITEM = (
    "<item><title>Okta reports Q3 results</title>"
    '<description>&lt;a href="https://x"&gt;Okta beats estimates&lt;/a&gt;</description>'
    '<source url="https://reuters.com">Reuters</source>'
    "<pubDate>Mon, 02 Dec 2024 10:00:00 GMT</pubDate></item>"
)


def test_news_parses_google_rss(fake_http):
    http = fake_http({"news.google.com": FakeResponse(rss(GOOGLE_ITEM))})
    articles = scraper.scrape_vendor_news("Okta Inc")
    assert articles == [
        {
            "title": "Okta reports Q3 results",
            "snippet": "Okta beats estimates",  # HTML stripped
            "source": "Reuters",
            "date": "Mon, 02 Dec 2024 10:00:00 GMT",
        }
    ]
    assert "q=Okta%20Inc" in http.urls[0]


def test_news_caps_articles(fake_http):
    fake_http({"news.google.com": FakeResponse(rss(*[GOOGLE_ITEM] * 8))})
    assert len(scraper.scrape_vendor_news("Okta", max_articles=3)) == 3


def test_news_missing_fields_become_empty_strings(fake_http):
    fake_http(
        {
            "news.google.com": FakeResponse(
                rss("<item><title>Only a title</title></item>")
            )
        }
    )
    assert scraper.scrape_vendor_news("Okta") == [
        {"title": "Only a title", "snippet": "", "source": "", "date": ""}
    ]


BING_FEED = rss(
    "<item><title>Okta on Bing</title><description>&lt;b&gt;Bold&lt;/b&gt; news</description></item>",
    "<item><description>no title, skipped</description></item>",
)


@pytest.mark.parametrize(
    "google",
    [FakeResponse("", status_code=503), FakeResponse(rss()), RuntimeError("timeout")],
    ids=["non-200", "empty-feed", "exception"],
)
def test_news_falls_back_to_bing(fake_http, google):
    http = fake_http({"news.google.com": google, "bing.com": FakeResponse(BING_FEED)})
    articles = scraper.scrape_vendor_news("Okta")
    assert articles == [
        {
            "title": "Okta on Bing",
            "snippet": "Bold news",
            "source": "Bing News",
            "date": "",
        }
    ]
    assert any("bing.com" in u for u in http.urls)


def test_news_returns_empty_list_when_both_sources_fail(fake_http):
    fake_http(
        {"news.google.com": RuntimeError("down"), "bing.com": RuntimeError("down")}
    )
    assert scraper.scrape_vendor_news("Okta") == []


# ── Legal news red flags ──────────────────────────────────


def test_legal_news_flags_only_signals_naming_the_vendor(fake_http):
    page = "OKTA DATA BREACH reported ... acme class action ... okta ftc inquiry"
    fake_http({"google.com/search": FakeResponse(page)})
    assert scraper._scrape_legal_news("Okta") == {
        "news_red_flags": [
            "Data breach mentioned in news",
            "FTC regulatory action detected in news",
        ]
    }


@pytest.mark.parametrize(
    "reply", [FakeResponse("nothing to see"), RuntimeError("blocked")]
)
def test_legal_news_returns_empty_without_flags_or_on_error(fake_http, reply):
    fake_http({"google.com/search": reply})
    assert scraper._scrape_legal_news("Okta") == {}


# ── scrape_vendor_compliance ──────────────────────────────


@pytest.fixture
def compliance_sources(monkeypatch):
    ofac = {"ofac_status": "clear", "ofac_clear": True, "ofac_matches": []}
    monkeypatch.setattr(scraper, "check_ofac", lambda name: dict(ofac))
    monkeypatch.setattr(
        scraper,
        "_scrape_legal_news",
        lambda name: {"news_red_flags": ["Data breach mentioned in news"]},
    )
    monkeypatch.setattr(scraper, "scrape_vendor_news", lambda name: [{"title": "t"}])


def test_compliance_combines_ofac_legal_flags_and_news(compliance_sources):
    data = scraper.scrape_vendor_compliance("Okta", industry="Software")
    assert data == {
        "industry": "Software",
        "ofac_status": "clear",
        "ofac_clear": True,
        "ofac_matches": [],
        "news_red_flags": ["Data breach mentioned in news"],
        "recent_news": [{"title": "t"}],
    }


def test_compliance_without_industry_omits_it(compliance_sources):
    assert "industry" not in scraper.scrape_vendor_compliance("Okta")


# ── Parsing helpers ───────────────────────────────────────


@pytest.mark.parametrize(
    "text, value",
    [
        ("$1.5B", 1_500_000_000.0),
        ("$250M", 250_000_000.0),
        ("$12K", 12_000.0),
        ("$1,234", 1234.0),
        (" 42.5 ", 42.5),
        ("n/a", None),
        ("", None),
    ],
)
def test_parse_dollar(text, value):
    assert scraper._parse_dollar(text) == value


@pytest.mark.parametrize(
    "val, divisor, expected",
    [
        (150, 100, 1.5),
        (1, 3, 0.3333),
        (None, 100, None),
        ("x", 100, None),
        (5, 0, None),
    ],
)
def test_safe_divide(val, divisor, expected):
    assert scraper._safe_divide(val, divisor) == expected
