# backend/app/services/compliance_discovery.py

"""
Single entry point for certification discovery.

Fix 2: Regulations never enter certification_evidence_tiers.
Fix 3: Web fallback skipped when 2+ VERIFIED_REGISTRY certs already found.
"""

from app.log import get_logger
from app.services.cert_pipeline import (
    discover_and_extract_certifications as discover_via_web,
)
from app.services.csa_star_connector import check_csa_star
from app.services.edgar_pipeline import discover_certifications_from_edgar
from app.services.fedramp_connector import check_fedramp

log = get_logger(__name__)

# Regulations and compliance frameworks that companies are subject to
# or help clients comply with — not certifications they hold themselves.
REGULATORY_ONLY = {
    "GDPR", "CCPA", "SOX", "FCPA",    # data/financial regulations
    "HIPAA",                            # US health data regulation
    "EMA", "FDA", "GxP", "GMP",        # pharma/life sciences regulations
    "FISMA", "FERC", "NERC",           # US government/energy regulations
    "PCI",                              # keep PCI_DSS cert but filter raw "PCI" mentions
}

async def discover_certifications(
    company_name: str, ticker_symbol: str | None = None
) -> dict:
    tiers: dict = {}
    evidence_list: list = []
    regulatory_disclosures: set = set()
    sources_used: list = []

    # ── 1. SEC EDGAR ──────────────────────────────────────────────────────
    log.info("cert_discovery.source_started", vendor=company_name, source="edgar")
    edgar_result = await discover_certifications_from_edgar(company_name, ticker_symbol)
    edgar_cik = edgar_result.get("edgar_cik")
    edgar_url = edgar_result.get("edgar_filing_url")

    if edgar_result.get("certifications_found"):
        for cert in edgar_result["certifications_found"]:
            if cert not in REGULATORY_ONLY:
                tiers[cert] = "VERIFIED_REGISTRY"
        evidence_list.extend(edgar_result.get("evidence", []))
        sources_used.append(edgar_url or "EDGAR")

    regulatory_disclosures.update(
        edgar_result.get("regulatory_frameworks_disclosed", [])
    )

    # ── 2. CSA STAR ───────────────────────────────────────────────────────
    log.info("cert_discovery.source_started", vendor=company_name, source="csa_star")
    csa = check_csa_star(company_name)
    if csa["csa_star_found"]:
        tiers["CSA_STAR"] = csa["csa_star_tier"]
        evidence_list.append(f"CSA_STAR — {csa['csa_star_evidence']}")
        sources_used.append("CSA STAR Registry")

    # ── 3. FedRAMP ────────────────────────────────────────────────────────
    log.info("cert_discovery.source_started", vendor=company_name, source="fedramp")
    fedramp = check_fedramp(company_name)
    if fedramp["fedramp_found"]:
        tiers["FedRAMP"] = fedramp["fedramp_tier"]
        evidence_list.append(f"FedRAMP — {fedramp['fedramp_evidence']}")
        sources_used.append("FedRAMP Marketplace")

    # ── 4. Web fallback ───────────────────────────────────────────────────
    # Require 3+ certs from EDGAR before skipping web fallback.
    # A single cert found in a risk disclosure (e.g. "risk of losing FedRAMP
    # authorization") isn't sufficient evidence that we've captured the
    # full cert profile. 3+ certs means EDGAR found a substantive compliance
    # section worth trusting, not just a passing mention.
    edgar_found_certs = len(edgar_result.get("certifications_found", [])) >= 3

    if edgar_found_certs:
        log.info("cert_discovery.web_fallback_skipped", vendor=company_name,
                 edgar_certs=len(edgar_result["certifications_found"]))

    should_run_web = not edgar_found_certs

    if should_run_web:
        import asyncio
        log.info("cert_discovery.web_fallback_started", vendor=company_name)
        await asyncio.sleep(5)  # cooldown between EDGAR and web extraction calls
        web_result = await discover_via_web(company_name)

        if web_result.get("certifications_found"):
            for cert in web_result["certifications_found"]:
                if cert not in REGULATORY_ONLY and cert not in tiers:
                    tiers[cert] = "CORROBORATED"
            evidence_list.extend(web_result.get("evidence", []))
            sources_used.extend(web_result.get("sources_checked", []))

        regulatory_disclosures.update(
            web_result.get("regulatory_frameworks_disclosed", [])
        )

    return {
        "certifications_found": list(tiers.keys()),
        "certification_evidence_tiers": tiers,
        "evidence": evidence_list,
        "regulatory_frameworks_disclosed": sorted(regulatory_disclosures),
        "sources_checked": sources_used,
        "edgar_cik": edgar_cik,
        "edgar_filing_url": edgar_url,
    }