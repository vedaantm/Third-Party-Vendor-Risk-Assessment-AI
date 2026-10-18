# backend/app/services/fedramp_connector.py

"""
FedRAMP Marketplace connector.

FedRAMP's marketplace site is a JS-rendered SPA with no confirmed public
API. The 18F/fedramp-data GitHub repository publishes the underlying
dataset as machine-readable JSON — public domain (CC0), no auth needed.

Note on freshness: this dataset lags the live marketplace by days to
weeks. For most vendor-risk evaluation purposes (checking whether a vendor
has ever achieved FedRAMP authorization) this is acceptable.

Designation tiers:
  "FedRAMP Authorized"   → VERIFIED_REGISTRY (full ATO granted)
  "FedRAMP Ready"        → CORROBORATED  (3PAO readiness assessment passed)
  "In Process"           → CORROBORATED  (authorization in progress)
"""

import requests

from app.services.name_matching import names_match

FEDRAMP_DATA_URL = (
    "https://raw.githubusercontent.com/"
    "18F/fedramp-data/master/data/data.json"
)

_fedramp_cache: list | None = None


def _load_fedramp_data() -> list:
    global _fedramp_cache
    if _fedramp_cache is not None:
        return _fedramp_cache
    try:
        resp = requests.get(FEDRAMP_DATA_URL, timeout=15)
        _fedramp_cache = resp.json().get("data", {}).get("Providers", [])
        print(f"  → FedRAMP data loaded: {len(_fedramp_cache)} entries")
    except Exception as e:
        print(f"  → FedRAMP data load failed: {e}")
        _fedramp_cache = []
    return _fedramp_cache


def check_fedramp(company_name: str) -> dict:
    """
    Returns:
      fedramp_tier:     "VERIFIED_REGISTRY" | "CORROBORATED" | None
      fedramp_evidence: human-readable evidence string | None
      fedramp_found:    bool
    """
    empty = {"fedramp_tier": None, "fedramp_evidence": None, "fedramp_found": False}

    entries = _load_fedramp_data()
    if not entries:
        return empty

    matches = []
    for entry in entries:
        pkg = entry.get("Cloud_Service_Provider_Package", "")
        csp = entry.get("Cloud_Service_Provider_Name", "")
        if names_match(company_name, pkg) or names_match(company_name, csp):
            matches.append(entry)

    if not matches:
        print(f"  → {company_name} not found in FedRAMP data")
        return empty

    authorized = [
    m for m in matches
    if any(word in (m.get("Designation", "") + m.get("Stage", "")).lower()
           for word in ("authorized", "compliant"))
    and "in process" not in (m.get("Designation", "") + m.get("Stage", "")).lower()
    ]
    partial = [
        m for m in matches
        if not authorized and (
            "ready" in (m.get("Designation", "") + m.get("Stage", "")).lower()
            or "in process" in (m.get("Designation", "") + m.get("Stage", "")).lower()
        )
    ]

    if authorized:
        best = authorized[0]
        tier = "VERIFIED_REGISTRY"
        evidence = (
            f"FedRAMP Authorization confirmed: "
            f"'{best.get('Cloud_Service_Provider_Package')}' "
            f"(designation: {best.get('Designation', 'Authorized')}, "
            f"impact level: {best.get('Impact_Level', 'unknown')}, "
            f"authorized: {best.get('Original_Authorization_Date', 'unknown')}). "
            f"Source: marketplace.fedramp.gov (data via 18F/fedramp-data)"
        )
    elif partial:
        best = partial[0]
        tier = "CORROBORATED"
        evidence = (
            f"FedRAMP authorization in progress: "
            f"'{best.get('Cloud_Service_Provider_Package')}' "
            f"(stage: {best.get('Stage', best.get('Designation', 'unknown'))}). "
            f"Not yet fully authorized — shows real intent and independent "
            f"readiness validation, but authorization is not complete. "
            f"Source: marketplace.fedramp.gov (data via 18F/fedramp-data)"
        )
    else:
        return empty

    print(f"  → FedRAMP result: {tier}")
    return {
        "fedramp_tier": tier,
        "fedramp_evidence": evidence,
        "fedramp_found": True,
    }