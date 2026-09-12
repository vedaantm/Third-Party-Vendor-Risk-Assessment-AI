# backend/test_registry_connectors.py
import sys
sys.path.insert(0, ".")

from app.services.fedramp_connector import check_fedramp
from app.services.csa_star_connector import check_csa_star

COMPANIES = ["Microsoft", "Infosys", "TCS"]

print("=" * 55)
print("FedRAMP check (no key needed — runs immediately)")
print("=" * 55)
for company in COMPANIES:
    print(f"\n--- {company} ---")
    result = check_fedramp(company)
    print(f"  found:    {result['fedramp_found']}")
    print(f"  tier:     {result['fedramp_tier']}")
    if result["fedramp_evidence"]:
        print(f"  evidence: {result['fedramp_evidence'][:120]}...")

print("\n" + "=" * 55)
print("CSA STAR check (seed dataset — no key needed)")
print("=" * 55)
for company in COMPANIES:
    print(f"\n--- {company} ---")
    result = check_csa_star(company)
    print(f"  found:    {result['csa_star_found']}")
    print(f"  tier:     {result['csa_star_tier']}")
    if result["csa_star_evidence"]:
        print(f"  evidence: {result['csa_star_evidence'][:120]}...")