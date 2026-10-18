# backend/app/services/csa_star_connector.py

"""
CSA STAR Registry connector — demo/seed-dataset mode.

WHY SEED DATA:
The CSA STAR API (star.watch/api/v1/registry) requires a paid corporate
membership (~$thousands/year). For this project we use a curated seed
dataset of companies with publicly known CSA STAR status, verified against
the public registry at cloudsecurityalliance.org/star/registry.

UPGRADING TO LIVE API:
Set DEMO_MODE = False and add CSA_STAR_API_KEY to .env. The
_check_live() function below implements the full API spec — nothing
else in the pipeline needs to change.

TIER MAPPING:
  Level 2: STARCertification, STARAttestation → VERIFIED_REGISTRY
  Level 1: SelfAssessment                     → CORROBORATED
"""

import os
import requests

from app.services.name_matching import names_match

DEMO_MODE = True
CSA_BASE = "https://star.watch/api/v1/registry"

SEED_DATA: list[dict] = [
    # Level 2 = third-party audited (VERIFIED_REGISTRY)
    {"name": "Microsoft",   "level": 2, "services": ["Azure", "Office 365"]},
    {"name": "Amazon",      "level": 2, "services": ["AWS"]},
    {"name": "Google",      "level": 2, "services": ["Google Cloud Platform"]},
    {"name": "IBM",         "level": 2, "services": ["IBM Cloud"]},
    {"name": "Salesforce",  "level": 2, "services": ["Salesforce CRM"]},
    {"name": "Oracle",      "level": 2, "services": ["Oracle Cloud Infrastructure"]},
    {"name": "SAP",         "level": 2, "services": ["SAP Cloud Platform"]},
    {"name": "ServiceNow",  "level": 2, "services": ["ServiceNow Platform"]},
    {"name": "Box",         "level": 2, "services": ["Box"]},
    {"name": "Zoom",        "level": 2, "services": ["Zoom"]},
    {"name": "Dropbox",     "level": 2, "services": ["Dropbox Business"]},
    {"name": "Workday",     "level": 2, "services": ["Workday HCM"]},
    {"name": "CrowdStrike", "level": 2, "services": ["Falcon Platform"]},
    {"name": "Palo Alto Networks", "level": 2, "services": ["Prisma Cloud"]},
    {"name": "Okta",        "level": 2, "services": ["Okta Identity Cloud"]},
    {"name": "Zscaler",     "level": 2, "services": ["Zscaler Internet Access"]},
    # Level 1 = self-assessment publicly filed with CSA (CORROBORATED)
    {"name": "Accenture",   "level": 1, "services": ["Accenture Cloud"]},
    {"name": "Infosys",     "level": 1, "services": ["Infosys Cloud"]},
    {"name": "Wipro",       "level": 1, "services": ["Wipro Cloud"]},
    {"name": "Cognizant",   "level": 1, "services": ["Cognizant Cloud"]},
    {"name": "HCL",         "level": 1, "services": ["HCL Cloud"]},
    {"name": "Tata Consultancy Services", "level": 2, "services": ["TCS Enterprise Cloud Platform"]},
]


def _check_seed(company_name: str) -> dict:
    for entry in SEED_DATA:
        if names_match(company_name, entry["name"]):
            tier = "VERIFIED_REGISTRY" if entry["level"] == 2 else "CORROBORATED"
            label = "third-party certified (Level 2)" if entry["level"] == 2 else "self-assessment filed (Level 1)"
            return {
                "csa_star_tier": tier,
                "csa_star_evidence": (
                    f"CSA STAR {label}: {entry['name']} — "
                    f"services: {', '.join(entry['services'])}. "
                    f"Source: cloudsecurityalliance.org/star/registry "
                    f"[demo dataset — architecture ready for live API]"
                ),
                "csa_star_found": True,
            }
    return {"csa_star_tier": None, "csa_star_evidence": None, "csa_star_found": False}


_org_cache: list | None = None


def _live_headers() -> dict | None:
    key = os.environ.get("CSA_STAR_API_KEY", "").strip()
    if not key:
        return None
    return {"Authorization": f"Bearer {key}"}


def _load_orgs(headers: dict) -> list:
    global _org_cache
    if _org_cache is not None:
        return _org_cache
    try:
        resp = requests.get(f"{CSA_BASE}/organizations", headers=headers, timeout=15)
        _org_cache = resp.json().get("organizations", [])
    except Exception as e:
        print(f"  → CSA STAR org list load failed: {e}")
        _org_cache = []
    return _org_cache


def _check_live(company_name: str) -> dict:
    empty = {"csa_star_tier": None, "csa_star_evidence": None, "csa_star_found": False}
    headers = _live_headers()
    if not headers:
        print("  → CSA_STAR_API_KEY not set")
        return empty
    orgs = _load_orgs(headers)
    org = next((o for o in orgs if names_match(company_name, o.get("name", ""))), None)
    if not org:
        return empty
    org_id = org["id"]
    try:
        resp = requests.get(f"{CSA_BASE}/organizations/{org_id}", headers=headers, timeout=15)
        services = resp.json().get("cloud_services", [])
    except Exception:
        return empty
    third_party = {"STARCertification", "STARAttestation", "CSTARCertification"}
    entries = []
    for svc in services:
        try:
            sr = requests.get(f"{CSA_BASE}/cloud_services/{svc['id']}", headers=headers, timeout=15)
            for e in sr.json().get("registry_entries", []):
                e["_svc"] = svc.get("name", "")
                entries.append(e)
        except Exception:
            pass
    has_tp = any(e.get("type") in third_party for e in entries)
    has_sa = any(e.get("type") == "SelfAssessment" for e in entries)
    if has_tp:
        tier, label = "VERIFIED_REGISTRY", "third-party certified (Level 2)"
    elif has_sa:
        tier, label = "CORROBORATED", "self-assessment filed (Level 1)"
    else:
        return empty
    return {
        "csa_star_tier": tier,
        "csa_star_evidence": f"CSA STAR {label}: {org.get('name')}. Source: {CSA_BASE}/organizations/{org_id}",
        "csa_star_found": True,
    }


def check_csa_star(company_name: str) -> dict:
    if DEMO_MODE:
        result = _check_seed(company_name)
        status = result["csa_star_tier"] if result["csa_star_found"] else "not in seed dataset"
        print(f"  → CSA STAR (demo): {status}")
        return result
    return _check_live(company_name)