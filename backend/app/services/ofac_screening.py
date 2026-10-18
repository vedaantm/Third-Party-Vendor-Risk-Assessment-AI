# backend/app/services/ofac_screening.py

"""
OFAC SDN screening.

Replaces the old substring check in scraper.py, which:
  - fetched a JSON endpoint that no longer exists, so every check errored
  - treated any error as "clear" (fail-open)
  - matched by raw substring, so short names hit unrelated entries

Result statuses:
  clear           no SDN entity/individual name or alias matches
  match           exact core-name match against an SDN name or alias,
                  and the name is specific (2+ words, not all generic) → REJECT
  possible_match  whole-word overlap with an SDN name or alias, or an
                  exact match on a single-word / generic name         → REVIEW
  unavailable     the SDN list could not be loaded                   → REVIEW

ofac_clear is True only for "clear", False only for "match", and None
otherwise — "we don't know" is never reported as clear.
"""

import csv
import io
import time

import requests

from app.services.name_matching import core_tokens, tokens_overlap

SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
ALT_URL = "https://www.treasury.gov/ofac/downloads/alt.csv"

SDN_CACHE_TTL_SECONDS = 24 * 60 * 60
DOWNLOAD_TIMEOUT_SECONDS = 30
MAX_REPORTED_MATCHES = 5

# Vessels and aircraft are listed by craft name ("OCEAN STAR"), not by the
# legal entity a vendor would contract with — skip them to avoid noise.
_SKIPPED_SDN_TYPES = {"vessel", "aircraft"}

# Words too common in company names to identify an entity on their own.
# A name made only of these (plus connectors) — or a single word — that
# exactly matches an SDN entry is reported as possible_match, not match:
# "Atlas" equals "ATLAS HOLDING" once the legal suffix is removed, but a
# vendor called Atlas is far more likely to be someone else. Heuristic —
# extend it as false positives are found.
GENERIC_NAME_WORDS = {
    # connectors
    "of", "and", "the", "for", "de", "del", "la", "le", "al", "el",
    # corporate descriptors
    "global", "international", "intl", "general", "united", "national",
    "universal", "worldwide", "world", "central", "first", "new", "prime",
    "premier", "royal", "golden", "star", "delta", "atlas", "alpha", "omega",
    "trading", "trade", "traders", "import", "export", "industries",
    "industrial", "industry", "enterprises", "enterprise", "services",
    "service", "solutions", "systems", "technologies", "technology", "tech",
    "consulting", "management", "partners", "associates", "ventures",
    "capital", "investment", "investments", "finance", "financial", "bank",
    "fund", "trust", "resources", "development", "construction",
    "engineering", "logistics", "shipping", "transport", "transportation",
    "cargo", "freight", "express", "marine", "maritime", "energy", "power",
    "oil", "gas", "petroleum", "petro", "chemical", "chemicals", "steel",
    "metals", "mining", "electronics", "electric", "media", "network",
    "networks", "data", "digital", "smart", "supply", "supplies", "parts",
    "equipment", "materials", "products", "commercial", "agency",
    # colours, directions, regions
    "red", "blue", "green", "black", "white", "east", "west", "north",
    "south", "eastern", "western", "northern", "southern", "middle",
    "pacific", "atlantic", "gulf", "ocean", "sea", "sky", "air",
}


def _name_is_specific(tokens: list[str]) -> bool:
    """At least two words, at least one of them not generic."""
    return len(tokens) >= 2 and any(t not in GENERIC_NAME_WORDS for t in tokens)


_sdn_cache: list[dict] | None = None
_sdn_loaded_at: float = 0.0


def _parse_sdn_csv(sdn_text: str, alt_text: str) -> list[dict]:
    """
    sdn.csv: ent_num, SDN_Name, SDN_Type, Program, ...
    alt.csv: ent_num, alt_num, alt_type, alt_name, remarks
    Returns [{"ent_num", "name", "program", "aliases": [...],
              "token_lists": [core tokens of name and each alias]}].
    """
    entries: dict[str, dict] = {}
    for row in csv.reader(io.StringIO(sdn_text)):
        if len(row) < 4:
            continue
        ent_num, name, sdn_type, program = (c.strip() for c in row[:4])
        if sdn_type.lower() in _SKIPPED_SDN_TYPES or not name:
            continue
        entries[ent_num] = {
            "ent_num": ent_num, "name": name, "program": program, "aliases": [],
        }

    for row in csv.reader(io.StringIO(alt_text)):
        if len(row) < 4:
            continue
        ent_num, alt_name = row[0].strip(), row[3].strip()
        if ent_num in entries and alt_name and alt_name != "-0-":
            entries[ent_num]["aliases"].append(alt_name)

    for entry in entries.values():
        _index_entry(entry)
    return list(entries.values())


def _index_entry(entry: dict) -> dict:
    """Pre-compute core tokens once, rather than on every screening call."""
    entry["token_lists"] = [
        t for t in (core_tokens(n) for n in [entry["name"], *entry["aliases"]]) if t
    ]
    return entry


def _load_sdn_entries() -> list[dict] | None:
    """
    Downloads the SDN list at most once per TTL. If a refresh fails, the
    previously loaded list is reused; if nothing was ever loaded, returns None.
    """
    global _sdn_cache, _sdn_loaded_at

    if _sdn_cache is not None and time.time() - _sdn_loaded_at < SDN_CACHE_TTL_SECONDS:
        return _sdn_cache

    try:
        sdn = requests.get(SDN_URL, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        alt = requests.get(ALT_URL, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        sdn.raise_for_status()
        alt.raise_for_status()
        entries = _parse_sdn_csv(sdn.text, alt.text)
        if not entries:
            raise ValueError("SDN list parsed to zero entries")
        _sdn_cache, _sdn_loaded_at = entries, time.time()
        print(f"  ✅ OFAC SDN list loaded: {len(entries)} entries")
    except Exception as e:
        print(f"  ⚠️  OFAC SDN list download failed: {e}")
        if _sdn_cache is None:
            return None
        print("  → Using previously loaded SDN list")

    return _sdn_cache


def screen_name(company_name: str, entries: list[dict] | None) -> dict:
    """Pure screening logic — no I/O, so it can be unit-tested directly."""
    if entries is None:
        return {
            "ofac_status": "unavailable",
            "ofac_clear": None,
            "ofac_matches": [],
        }

    query = core_tokens(company_name)
    if not query:
        # Nothing meaningful to screen (empty / punctuation-only name)
        return {"ofac_status": "unavailable", "ofac_clear": None, "ofac_matches": []}

    specific = _name_is_specific(query)
    exact, exact_generic, overlap = [], [], []
    for entry in entries:
        token_lists = entry.get("token_lists")
        if token_lists is None:
            token_lists = _index_entry(entry)["token_lists"]
        label = f"{entry['name']} [{entry['program']}] (SDN #{entry['ent_num']})"
        # An exact hit on any name or alias outranks a partial hit on another
        if any(t == query for t in token_lists):
            if specific:
                exact.append(label)
            else:
                exact_generic.append(f"{label} — exact name match, but the name "
                                     f"is too short or generic to confirm")
        elif any(tokens_overlap(query, t) for t in token_lists):
            overlap.append(label)
    # Exact-but-generic hits first, so the report cap never hides them
    possible = exact_generic + overlap

    if exact:
        return {
            "ofac_status": "match",
            "ofac_clear": False,
            "ofac_matches": exact[:MAX_REPORTED_MATCHES],
        }
    if possible:
        return {
            "ofac_status": "possible_match",
            "ofac_clear": None,
            "ofac_matches": possible[:MAX_REPORTED_MATCHES],
        }
    return {"ofac_status": "clear", "ofac_clear": True, "ofac_matches": []}


def check_ofac(company_name: str) -> dict:
    result = screen_name(company_name, _load_sdn_entries())
    status = result["ofac_status"]
    if status == "clear":
        print(f"  ✅ OFAC: {company_name} — no SDN match")
    else:
        print(f"  ⚠️  OFAC: {company_name} — {status} {result['ofac_matches']}")
    return result
