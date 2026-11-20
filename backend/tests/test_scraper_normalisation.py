"""
Regression tests for three scraper bugs:

1. "Credit Services" (Visa, Mastercard, PayPal) matched the "it services"
   key by substring and was classified as IT Services.
2. Yahoo's "Software—Infrastructure" (Okta, Microsoft, Cloudflare) was left
   unmapped because only the spaced spelling was in the map.
3. A real 0 (debt-free, break-even, flat growth) was treated as missing and
   dropped, so agents saw "not provided".
"""

from types import SimpleNamespace

import pytest

from app.services import scraper


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Credit Services", "Financial Services"),
        ("Software—Infrastructure", "Software"),
        ("Software - Infrastructure", "Software"),
        ("Software Infrastructure", "Software"),
        ("Software–Infrastructure", "Software"),
        ("Software—Application", "Software"),
        ("Software - Application", "Software"),
        ("Information Technology Services", "IT Services"),
        ("Banks—Regional", "Financial Services"),
        ("Insurance—Property & Casualty", "Financial Services"),
        ("Specialty Industrial Machinery", "Manufacturing"),
        ("Industrials", "Manufacturing"),  # only the start of a keyword is anchored
        ("Medical Devices", "Healthcare"),
        ("Telecom Services", "Telecommunications"),
    ],
)
def test_industry_mapping(raw, expected):
    assert scraper._normalize_yfinance_industry(raw) == expected


@pytest.mark.parametrize("raw", ["Gold", "Semiconductors", "Audit Services"])
def test_keywords_do_not_match_inside_other_words(raw):
    # "Audit Services" contains "it services" mid-word, like "Credit Services" did
    assert scraper._normalize_yfinance_industry(raw) == raw


@pytest.mark.parametrize("raw", ["", None])
def test_empty_industry_is_returned_unchanged(raw):
    assert scraper._normalize_yfinance_industry(raw) == raw


def test_industry_text_treats_dashes_as_spaces():
    assert (
        scraper._industry_text("  Software—Infrastructure ")
        == "software infrastructure"
    )
    assert scraper._industry_text("Software - Application") == "software application"
    assert scraper._industry_text("E-Commerce") == "e-commerce"  # unspaced hyphen kept


def scrape(monkeypatch, info):
    ticker = SimpleNamespace(info={"longName": "Okta, Inc.", **info}, financials=None)
    monkeypatch.setattr(scraper, "_find_ticker", lambda name: "OKTA")
    monkeypatch.setattr(scraper, "yf", SimpleNamespace(Ticker=lambda symbol: ticker))
    return scraper._scrape_yfinance("Okta")


def test_zero_values_are_kept(monkeypatch):
    data = scrape(
        monkeypatch,
        {
            "debtToEquity": 0,
            "profitMargins": 0.0,
            "grossMargins": 0.0,
            "revenueGrowth": 0.0,
        },
    )
    assert data["debt_equity_ratio"] == 0.0
    assert data["net_margin"] == 0.0
    assert data["gross_margin"] == 0.0
    assert data["revenue_growth"] == 0.0


def test_missing_values_are_still_dropped(monkeypatch):
    data = scrape(monkeypatch, {})
    for key in ("debt_equity_ratio", "net_margin", "gross_margin", "revenue_growth"):
        assert key not in data


def test_non_zero_values_are_converted(monkeypatch):
    data = scrape(
        monkeypatch,
        {
            "debtToEquity": 150.0,
            "profitMargins": -0.052,
            "grossMargins": 0.7634,
            "revenueGrowth": 0.12,
        },
    )
    assert data["debt_equity_ratio"] == 1.5
    assert data["net_margin"] == -5.2
    assert data["gross_margin"] == 76.34
    assert data["revenue_growth"] == 12.0


@pytest.mark.parametrize(
    "val, divisor, expected",
    [(0, 100, 0.0), (150, 100, 1.5), (None, 100, None), ("x", 100, None), (5, 0, None)],
)
def test_safe_divide(val, divisor, expected):
    assert scraper._safe_divide(val, divisor) == expected


@pytest.mark.parametrize(
    "fraction, expected", [(0, 0.0), (0.139, 13.9), (-0.5, -50.0), (None, None)]
)
def test_percent(fraction, expected):
    assert scraper._percent(fraction) == expected
