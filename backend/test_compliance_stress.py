# backend/test_compliance_stress.py
# Run from backend/ with: python test_compliance_stress.py

import asyncio
import time
import sys
sys.path.insert(0, ".")

from app.services.compliance_discovery import discover_certifications

COMPANIES = [
    # Large public — expect EDGAR to fire
    ("Microsoft",     "large_public",   "EDGAR + CSA STAR L2 + FedRAMP expected"),
    ("Accenture",     "large_public",   "EDGAR expected; CSA STAR L1 in seed"),
    ("IBM",           "large_public",   "EDGAR + CSA STAR L2 expected"),
    ("Wipro",         "large_public",   "Foreign filer (20-F); CSA STAR L1 in seed"),

    # Cloud-native / SaaS — CSA STAR + FedRAMP likely
    ("Salesforce",    "cloud_native",   "CSA STAR L2 + FedRAMP expected"),
    ("Okta",          "cloud_native",   "CSA STAR L2 + FedRAMP expected"),
    ("Zoom",          "cloud_native",   "CSA STAR L2; FedRAMP partial expected"),
    ("CrowdStrike",   "cloud_native",   "CSA STAR L2; FedRAMP likely"),

    # Mid-market public — may or may not be in EDGAR
    ("Qualys",        "mid_market",     "US-listed cybersecurity; EDGAR likely"),
    ("Rapid7",        "mid_market",     "US-listed cybersecurity; EDGAR likely"),
    ("Tenable",       "mid_market",     "US-listed; EDGAR likely"),
    ("Mimecast",      "mid_market",     "Went private 2022; tests EDGAR miss + web fallback"),

    # Niche / private / small — web fallback only
    ("Vanta",         "niche_private",  "Private compliance SaaS; no EDGAR; web fallback"),
    ("Drata",         "niche_private",  "Private GRC SaaS; no EDGAR; web fallback"),
    ("Strike Graph",  "niche_private",  "Small private; tests multi-word name handling"),
    ("Schellman",     "niche_private",  "Audit firm; niche, likely web only"),

    # Edge cases
    ("TCS",           "edge_case",      "Ticker collision risk: TCS.NS not in SEC"),
    ("HCL",           "edge_case",      "Short 3-letter name; CSA STAR seed match expected"),
    ("Infosys BPM",   "edge_case",      "Subsidiary name; tests whether parent (INFY) resolves"),
    ("SAP SE",        "edge_case",      "Foreign filer with suffix; tests name normalization"),
]

results = []


async def run_one(company_name: str, category: str, notes: str):
    print(f"\n{'─'*60}")
    print(f"[{category.upper()}] {company_name}")
    print(f"  Notes: {notes}")
    t0 = time.time()
    try:
        result = await discover_certifications(company_name)
        elapsed = round(time.time() - t0, 1)

        cik     = result.get("edgar_cik")
        filing  = result.get("edgar_filing_url")
        tiers   = result.get("certification_evidence_tiers", {})
        certs   = result.get("certifications_found", [])
        sources = result.get("sources_checked", [])
        evidence = result.get("evidence", [])

        fired = []
        if filing:
            fired.append("EDGAR")
        if any("CSA" in str(s) for s in sources):
            fired.append("CSA_STAR")
        if any("FedRAMP" in str(s) for s in sources):
            fired.append("FedRAMP")
        if any("sec.gov" not in str(s) and s not in
               ["CSA STAR Registry", "FedRAMP Marketplace"]
               for s in sources if s):
            fired.append("web_fallback")
        if not fired:
            fired.append("none")

        print(f"  ✓ Completed in {elapsed}s")
        print(f"  Sources fired : {', '.join(fired)}")
        print(f"  CIK resolved  : {cik or 'N/A (not SEC-listed)'}")
        print(f"  Certs found   : {certs if certs else 'none'}")
        if tiers:
            print(f"  Tiers:")
            for cert, tier in tiers.items():
                print(f"      {cert:20s} → {tier}")
        if evidence:
            print(f"  Evidence [{len(evidence)} item(s)]:")
            for e in evidence[:2]:
                print(f"    • {str(e)[:120]}")
            if len(evidence) > 2:
                print(f"    ... and {len(evidence)-2} more")

        results.append({
            "company": company_name, "category": category,
            "status": "ok", "elapsed": elapsed,
            "sources_fired": fired, "cik": cik,
            "certs_found": certs, "tiers": tiers,
        })

    except Exception as exc:
        elapsed = round(time.time() - t0, 1)
        print(f"  ✗ THREW after {elapsed}s: {type(exc).__name__}: {exc}")
        results.append({
            "company": company_name, "category": category,
            "status": "error", "elapsed": elapsed,
            "error": str(exc),
        })


async def main():
    print("=" * 60)
    print("COMPLIANCE DISCOVERY — STRESS TEST")
    print(f"Testing {len(COMPANIES)} companies across 5 categories")
    print("=" * 60)

    for company, category, notes in COMPANIES:
        await run_one(company, category, notes)

    print(f"\n{'='*60}")
    print("STRESS TEST SUMMARY")
    print(f"{'='*60}")

    by_category: dict[str, list] = {}
    for r in results:
        by_category.setdefault(r["category"], []).append(r)

    total_ok    = sum(1 for r in results if r["status"] == "ok")
    total_err   = sum(1 for r in results if r["status"] == "error")
    total_found = sum(1 for r in results if r.get("certs_found"))
    edgar_hits  = sum(1 for r in results if "EDGAR" in r.get("sources_fired", []))
    web_hits    = sum(1 for r in results if "web_fallback" in r.get("sources_fired", []))
    threw       = [r["company"] for r in results if r["status"] == "error"]
    no_certs    = [r["company"] for r in results
                   if r["status"] == "ok" and not r.get("certs_found")]

    print(f"\nOverall: {total_ok}/{len(COMPANIES)} completed without error")
    print(f"  Certs found    : {total_found}/{len(COMPANIES)}")
    print(f"  EDGAR fired    : {edgar_hits}/{len(COMPANIES)}")
    print(f"  Web fallback   : {web_hits}/{len(COMPANIES)}")
    print(f"  Errors (threw) : {total_err} — {threw if threw else 'none'}")
    print(f"  No certs found : {no_certs if no_certs else 'none'}")

    print(f"\nBy category:")
    for cat, cat_results in by_category.items():
        ok    = sum(1 for r in cat_results if r["status"] == "ok")
        found = sum(1 for r in cat_results if r.get("certs_found"))
        avg_t = round(sum(r["elapsed"] for r in cat_results) / len(cat_results), 1)
        print(f"  {cat:20s}: {ok}/{len(cat_results)} ok | "
              f"{found}/{len(cat_results)} found certs | avg {avg_t}s")

    print(f"\nPer-company timing:")
    for r in sorted(results, key=lambda x: x["elapsed"], reverse=True):
        status = "✓" if r["status"] == "ok" else "✗"
        certs  = len(r.get("certs_found", []))
        fired  = ", ".join(r.get("sources_fired", []))
        print(f"  {status} {r['company']:25s} {r['elapsed']:5.1f}s  "
              f"certs={certs}  via={fired}")


asyncio.run(main())