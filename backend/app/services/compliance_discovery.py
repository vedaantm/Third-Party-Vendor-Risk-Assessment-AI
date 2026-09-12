# backend/app/services/compliance_discovery.py

"""
Single entry point for certification discovery.

Fix 2: Regulations never enter certification_evidence_tiers.
Fix 3: Web fallback skipped when 2+ VERIFIED_REGISTRY certs already found.
"""

from app.services.edgar_pipeline import discover_certifications_from_edgar
from app.services.cert_pipeline import discover_and_extract_certifications as discover_via_web
from app.services.csa_star_connector import check_csa_star
from app.services.fedramp_connector import check_fedramp

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
    print(f"\n[compliance_discovery] EDGAR check for {company_name}")
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
    print(f"\n[compliance_discovery] CSA STAR check for {company_name}")
    csa = check_csa_star(company_name)
    if csa["csa_star_found"]:
        tiers["CSA_STAR"] = csa["csa_star_tier"]
        evidence_list.append(f"CSA_STAR — {csa['csa_star_evidence']}")
        sources_used.append("CSA STAR Registry")

    # ── 3. FedRAMP ────────────────────────────────────────────────────────
    print(f"\n[compliance_discovery] FedRAMP check for {company_name}")
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
        print(
            f"\n[compliance_discovery] Skipping web fallback "
            f"(EDGAR found {len(edgar_result['certifications_found'])} certs — sufficient coverage)"
        )

    should_run_web = not edgar_found_certs

    if should_run_web:
        import asyncio
        print(
            f"\n[compliance_discovery] Web search fallback for {company_name} "
            f"(EDGAR found no specific certs)"
        )
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