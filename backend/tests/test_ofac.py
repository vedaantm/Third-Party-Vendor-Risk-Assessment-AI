"""
OFAC SDN screening.

Offline tests use tests/fixtures/{sdn,alt}_sample.csv: real rows (names,
entity numbers, types, programs and aliases verbatim; long remarks trimmed)
taken from Treasury's sdn.csv / alt.csv on 2026-10-17, chosen to cover known
positives and near-miss names of real companies.

Live tests download the full current list.
"""

import pytest

from app.services import ofac_screening
from app.services.ofac_screening import _parse_sdn_csv, check_ofac, screen_name
from conftest import FIXTURES


@pytest.fixture(scope="module")
def real_sample():
    return _parse_sdn_csv(
        (FIXTURES / "sdn_sample.csv").read_text(encoding="utf-8"),
        (FIXTURES / "alt_sample.csv").read_text(encoding="utf-8"),
    )


# Minimal synthetic rows for edge cases the real sample doesn't cover
SYNTHETIC_SDN = """\
535,"CIMEX",-0- ,"CUBA",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0-
900,"OCEAN STAR",vessel ,"IRAN",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0-
901,"SKY TRADER",aircraft ,"IRAN",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0-
"""


@pytest.fixture
def synthetic():
    return _parse_sdn_csv(SYNTHETIC_SDN, "")


# ── Parsing ───────────────────────────────────────────────

def test_parse_real_sample(real_sample):
    by_id = {e["ent_num"]: e for e in real_sample}
    assert len(by_id) == 10
    assert by_id["306"]["name"] == "BANCO NACIONAL DE CUBA"
    assert "ROSNEFT" in by_id["17022"]["aliases"]
    assert all(e["token_lists"] for e in real_sample)


def test_parse_skips_vessels_and_aircraft(synthetic):
    assert [e["name"] for e in synthetic] == ["CIMEX"]


def test_parse_ignores_aliases_for_unknown_or_skipped_entities():
    entries = _parse_sdn_csv(SYNTHETIC_SDN, '900,1,"aka","STAR OF THE SEA",-0- \n')
    assert all("STAR OF THE SEA" not in e["aliases"] for e in entries)


# ── Known positives / negatives (real SDN rows) ───────────

@pytest.mark.parametrize("vendor, sdn_id", [
    ("Rosneft", "17022"),                       # via alias "ROSNEFT"
    ("Rosneft Oil Company", "17022"),           # via alias "ROSNEFT OIL COMPANY"
    ("Banco Nacional de Cuba", "306"),
    ("Banco Nacional de Cuba, S.A.", "306"),    # legal suffix ignored
    ("National Bank of Cuba", "306"),           # English alias
    ("Sberbank of Russia", "17018"),            # via alias "SBERBANK OF RUSSIA"
    ("Atlas Holding SAL", "28527"),
])
def test_known_positive_is_match(real_sample, vendor, sdn_id):
    result = screen_name(vendor, real_sample)
    assert result["ofac_status"] == "match"
    assert result["ofac_clear"] is False
    assert any(f"SDN #{sdn_id}" in m for m in result["ofac_matches"])


@pytest.mark.parametrize("vendor, sdn_id", [
    ("Sberbank", "17018"),                      # SDN names carry legal-form prefixes (PJSC…)
    ("Gazprom", "17143"),                       # only "Gazprom Neft" is listed here
    ("Accenture", "30971"),                     # ACCENTURE BUILDING MATERIALS
    ("Apple", "54199"),                         # ORIENTAL APPLE COMPANY PTE LTD
    ("Box", "48803"),                           # RED BOX ENERGY SERVICES
])
def test_partial_name_overlap_is_possible_match(real_sample, vendor, sdn_id):
    result = screen_name(vendor, real_sample)
    assert result["ofac_status"] == "possible_match"
    assert result["ofac_clear"] is None
    assert any(f"SDN #{sdn_id}" in m for m in result["ofac_matches"])


def test_generic_single_word_exact_match_is_treated_as_match(real_sample):
    """
    Documents CURRENT behaviour, flagged as a policy question: "Atlas"
    exactly matches "ATLAS HOLDING" once the legal suffix is removed, so a
    legitimate vendor named Atlas is auto-rejected. The reason text names
    the SDN entry so a reviewer can override.
    """
    assert screen_name("Atlas", real_sample)["ofac_status"] == "match"


@pytest.mark.parametrize("vendor", [
    "Microsoft", "Infosys", "Workday", "Okta", "Salesforce",
    "Tata Consultancy Services", "CrowdStrike", "Rosneftegaz Trading",
    "Deltaparts", "Cubanacan Holdings",
])
def test_known_negative_is_clear(real_sample, vendor):
    result = screen_name(vendor, real_sample)
    assert result["ofac_status"] == "clear", result["ofac_matches"]
    assert result["ofac_clear"] is True
    assert result["ofac_matches"] == []


def test_exact_alias_outranks_partial_primary_name(real_sample):
    # Primary "OPEN JOINT-STOCK COMPANY ROSNEFT OIL COMPANY" only overlaps;
    # alias "ROSNEFT" is exact — the entity must be reported as a match
    assert screen_name("Rosneft", real_sample)["ofac_status"] == "match"


# ── Edge cases ────────────────────────────────────────────

def test_substring_inside_a_word_is_not_a_match(synthetic):
    # The old check flagged "cimex" in "cimexico"
    assert screen_name("Cimexico Corp", synthetic)["ofac_status"] == "clear"


def test_whole_word_overlap_with_extra_words_is_possible_match(synthetic):
    assert screen_name("Cimex Trading", synthetic)["ofac_status"] == "possible_match"


def test_vessel_names_do_not_flag_companies(synthetic):
    assert screen_name("Ocean Star Logistics", synthetic)["ofac_status"] == "clear"


def test_matches_are_capped(synthetic):
    many = [dict(e, ent_num=str(i)) for i, e in enumerate(synthetic * 10)]
    result = screen_name("Cimex", many)
    assert len(result["ofac_matches"]) == ofac_screening.MAX_REPORTED_MATCHES


def test_unindexed_entries_are_indexed_on_demand():
    entry = {"ent_num": "1", "name": "CIMEX", "program": "CUBA", "aliases": []}
    assert screen_name("Cimex", [entry])["ofac_status"] == "match"


def test_missing_list_is_unavailable_not_clear():
    result = screen_name("Microsoft", None)
    assert result["ofac_status"] == "unavailable"
    assert result["ofac_clear"] is None


@pytest.mark.parametrize("name", ["", "   ", ",. ", "Inc."])
def test_name_with_nothing_to_screen_is_unavailable(synthetic, name):
    assert screen_name(name, synthetic)["ofac_status"] == "unavailable"


# ── Loading and caching (check_ofac) ──────────────────────

class FakeResponse:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def fresh_cache(monkeypatch):
    monkeypatch.setattr(ofac_screening, "_sdn_cache", None)
    monkeypatch.setattr(ofac_screening, "_sdn_loaded_at", 0.0)


def test_check_ofac_downloads_both_files_once_and_caches(monkeypatch, fresh_cache):
    calls = []
    sdn = (FIXTURES / "sdn_sample.csv").read_text(encoding="utf-8")
    alt = (FIXTURES / "alt_sample.csv").read_text(encoding="utf-8")

    def fake_get(url, timeout):
        calls.append(url)
        return FakeResponse(sdn if url == ofac_screening.SDN_URL else alt)

    monkeypatch.setattr(ofac_screening.requests, "get", fake_get)
    assert check_ofac("Rosneft")["ofac_status"] == "match"
    assert check_ofac("Microsoft")["ofac_status"] == "clear"
    assert calls == [ofac_screening.SDN_URL, ofac_screening.ALT_URL]


def test_check_ofac_refreshes_after_ttl(monkeypatch, fresh_cache, real_sample):
    calls = []

    def fake_get(url, timeout):
        calls.append(url)
        return FakeResponse(SYNTHETIC_SDN if url == ofac_screening.SDN_URL else "")

    monkeypatch.setattr(ofac_screening, "_sdn_cache", real_sample)
    monkeypatch.setattr(ofac_screening, "_sdn_loaded_at", 0.0)   # long expired
    monkeypatch.setattr(ofac_screening.requests, "get", fake_get)
    assert check_ofac("Cimex")["ofac_status"] == "match"           # new list in use
    assert len(calls) == 2


def test_download_failure_fails_closed(monkeypatch, fresh_cache):
    def boom(*args, **kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr(ofac_screening.requests, "get", boom)
    result = check_ofac("Microsoft")
    assert result["ofac_status"] == "unavailable"
    assert result["ofac_clear"] is None


def test_http_error_fails_closed(monkeypatch, fresh_cache):
    # The original endpoint returned 404 and the old code reported "clear"
    monkeypatch.setattr(ofac_screening.requests, "get",
                        lambda url, timeout: FakeResponse("Not Found", 404))
    assert check_ofac("Microsoft")["ofac_status"] == "unavailable"


def test_empty_list_fails_closed(monkeypatch, fresh_cache):
    monkeypatch.setattr(ofac_screening.requests, "get",
                        lambda url, timeout: FakeResponse(""))
    assert check_ofac("Microsoft")["ofac_status"] == "unavailable"


def test_refresh_failure_reuses_previous_list(monkeypatch, real_sample):
    def boom(*args, **kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr(ofac_screening, "_sdn_cache", real_sample)
    monkeypatch.setattr(ofac_screening, "_sdn_loaded_at", 0.0)   # TTL expired
    monkeypatch.setattr(ofac_screening.requests, "get", boom)
    assert check_ofac("Rosneft")["ofac_status"] == "match"


# ── Live: full current SDN list ───────────────────────────

@pytest.mark.live
@pytest.mark.parametrize("vendor, expected", [
    ("Rosneft", "match"),
    ("Banco Nacional de Cuba", "match"),
    ("Sberbank", "possible_match"),
    ("Gazprom", "possible_match"),
    ("Microsoft", "clear"),
    ("Infosys", "clear"),
    ("Workday", "clear"),
    ("Okta", "clear"),
])
def test_live_known_results_against_current_sdn_list(vendor, expected):
    assert check_ofac(vendor)["ofac_status"] == expected
