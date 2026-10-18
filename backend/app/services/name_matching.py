# backend/app/services/name_matching.py

"""
Company-name matching shared by the registry connectors and OFAC screening.

The previous matchers accepted a match if the two names shared ANY word,
so "Acme Data Systems" matched every registry entry containing "data" or
"systems", and "Tata Motors" matched "Tata Consultancy Services".

Here names are reduced to an ordered list of core tokens (punctuation and
trailing legal-form suffixes removed) and compared as whole-token sequences.
"""

import re

# Legal-form words stripped from the END of a name only — "Bank of the West
# Group" keeps "the", but loses "group".
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "ltd", "limited", "llc", "lp", "llp", "plc", "pllc",
    "sa", "ag", "nv", "bv", "se", "gmbh", "spa", "srl", "pte", "pty",
    "holdings", "holding", "group",
}


def core_tokens(name: str) -> list[str]:
    """
    "Microsoft Corporation"         → ["microsoft"]
    "AMAZON COM INC"                → ["amazon", "com"]
    "Banco Nacional de Cuba, S.A."  → ["banco", "nacional", "de", "cuba"]
    """
    if not name:
        return []
    text = name.lower().replace("&", " and ")
    # Collapse dotted abbreviations so "S.A." → "sa" and "J.P." → "jp"
    text = re.sub(r"\b([a-z])\.(?=[a-z]\b)", r"\1", text)
    tokens = re.findall(r"[a-z0-9]+", text)

    if tokens and tokens[0] == "the":
        tokens = tokens[1:]
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens = tokens[:-1]
    return tokens


def _is_prefix(short: list[str], long: list[str]) -> bool:
    return bool(short) and long[:len(short)] == short


def _contains_sequence(needle: list[str], haystack: list[str]) -> bool:
    n = len(needle)
    return bool(needle) and any(
        haystack[i:i + n] == needle for i in range(len(haystack) - n + 1)
    )


def tokens_overlap(ta: list[str], tb: list[str]) -> bool:
    """One token sequence appears contiguously, as whole tokens, in the other."""
    return _contains_sequence(ta, tb) or _contains_sequence(tb, ta)


def names_exact(a: str, b: str) -> bool:
    """Same core name: "Microsoft" == "Microsoft Corporation"."""
    ta, tb = core_tokens(a), core_tokens(b)
    return bool(ta) and ta == tb


def names_match(query: str, candidate: str) -> bool:
    """
    Registry-style match: the core names are equal, or the vendor's name
    (`query`) is a whole-token prefix of the registry name — registries list
    product/package names that extend the company name.
        "Salesforce"         ~ "Salesforce Government Cloud"   (package name)
        "Okta, Inc."         ~ "Okta"
    A vendor name that merely contains a registry name is NOT a match:
        "Box Hill Logistics" ≁ "Box Inc."
        "Tata Motors"        ≁ "Tata Consultancy Services"
        "Acme Data Systems"  ≁ "Data Systems Inc"
    """
    tq, tc = core_tokens(query), core_tokens(candidate)
    if not tq or not tc:
        return False
    return _is_prefix(tq, tc)


def names_overlap(a: str, b: str) -> bool:
    """
    Looser screening match: one core name appears as a contiguous whole-token
    sequence inside the other. Used only to raise a *possible* sanctions match
    for human review — never as positive evidence.
    """
    return tokens_overlap(core_tokens(a), core_tokens(b))
