import asyncio
import json
import os
import traceback
from dotenv import load_dotenv

load_dotenv(dotenv_path=r"C:\Users\dell\Desktop\IBM WATSON\backend\.env")

from app.agents.financial_agent   import run_financial_agent
from app.agents.compliance_agent  import run_compliance_agent
from app.agents.operational_agent import run_operational_agent
from app.agents.reputation_agent  import run_reputation_agent
from app.agents.strategic_agent   import run_strategic_agent
from app.agents.meta_agent        import run_meta_agent
from app.services.scraper         import (
    scrape_vendor_financials,
    scrape_vendor_compliance
)

# ── Only change this ───────────────────────────────────────
VENDOR_NAME = "Microsoft"

CLIENT_PROFILE = {
    "company_name":     "Acme Manufacturing Corp",
    "industry":         "Manufacturing",
    "contract_value":   5_000_000,
    "contract_years":   3,
    "risk_appetite":    "medium",
    "priorities": [
        "cloud infrastructure and managed services",
        "cybersecurity and data protection",
        "scalability for international expansion",
        "24/7 enterprise support",
        "AI and automation capabilities"
    ],
    "dealbreakers": [
        "no SOC2 certification",
        "active OFAC sanctions",
        "criminal investigation",
        "less than 3 years in operation"
    ],
    "nice_to_have": [
        "ISO27001 certified",
        "proven track record in manufacturing sector",
        "strong R&D investment"
    ]
}
# ──────────────────────────────────────────────────────────

AGENTS = {
    "Financial":   run_financial_agent,
    "Compliance":  run_compliance_agent,
    "Operational": run_operational_agent,
    "Reputation":  run_reputation_agent,
    "Strategic":   run_strategic_agent,
}

# Maps agent name to the state key it writes to
OUTPUT_KEYS = {
    "Financial":   "financial_output",
    "Compliance":  "compliance_output",
    "Operational": "operational_output",
    "Reputation":  "reputation_output",
    "Strategic":   "strategic_output",
}


async def run_agent_safe(name: str, fn, state: dict) -> dict:
    """Runs an agent and catches any errors cleanly."""
    try:
        result = await fn(state)
        return {"status": "PASS", "result": result}
    except Exception as e:
        return {
            "status": "FAIL",
            "error": str(e),
            "traceback": traceback.format_exc(),
            "result": None
        }


async def main():
    print("=" * 60)
    print(f"  VENDOR RISK PLATFORM — Full Pipeline Test")
    print(f"  Vendor: {VENDOR_NAME}")
    print("=" * 60)

    print("\n[SCRAPER] Fetching financial data...")
    financial_data  = scrape_vendor_financials(VENDOR_NAME)

    industry = financial_data.get("industry", None)
    print(f"\n[SCRAPER] Detected industry: {industry}")

    print("\n[SCRAPER] Fetching compliance data...")
    compliance_data = scrape_vendor_compliance(VENDOR_NAME, industry=industry)

# ── Build state ────────────────────────────────────────
    state = {
        "vendor_id":           "v001",
        "vendor_name":         VENDOR_NAME,
        "company_id":          "acme-001",
        "evaluation_id":       "eval-full-test-001",
        "weight_financial":    0.30,
        "weight_compliance":   0.25,
        "weight_operational":  0.20,
        "weight_reputation":   0.10,
        "weight_strategic":    0.15,
        "financial_data":      financial_data or _fallback_financial(),
        "compliance_data":     compliance_data or {},

        # Pass vendor name + industry so agents have context to reason from
        "operational_data": {
            "vendor_name":        VENDOR_NAME,
            "industry":           financial_data.get("industry", "Unknown"),
            "years_in_operation": financial_data.get("years_in_operation"),
            "company_size": (
                "large"  if (financial_data.get("ebitda") or 0) > 1_000_000_000
                else "mid" if (financial_data.get("ebitda") or 0) > 100_000_000
                else "small"
            ),
            "ebitda":             financial_data.get("ebitda"),
            "revenue_growth":     financial_data.get("revenue_growth"),
        },
        "reputation_data": {
            "vendor_name":        VENDOR_NAME,
            "industry":           financial_data.get("industry", "Unknown"),
            "years_in_operation": financial_data.get("years_in_operation"),
            "certifications":     compliance_data.get("certifications_found", []),
            "news_red_flags":     compliance_data.get("news_red_flags", []),
            "ofac_clear":         compliance_data.get("ofac_clear", True),
            "revenue_growth":     financial_data.get("revenue_growth"),
        },
        "strategic_data": {
            "vendor_name":        VENDOR_NAME,
            "industry":           financial_data.get("industry", "Unknown"),
            "years_in_operation": financial_data.get("years_in_operation"),
            "revenue_growth":     financial_data.get("revenue_growth"),
            "gross_margin":       financial_data.get("gross_margin"),
            "ebitda":             financial_data.get("ebitda"),
            "certifications":     compliance_data.get("certifications_found", []),
            # ── Client context for matching ──────────────────────
            "client_profile":     CLIENT_PROFILE,
        },
        "pipeline_status":     "running",
        "conflict_detected":   False,
        "data_complete":       True,
        "missing_fields":      []
    }

    # ── Run all agents and collect results ─────────────────
    print("\n" + "=" * 60)
    print("  RUNNING ALL 5 AGENTS")
    print("=" * 60)

    test_results = {}

    for name, fn in AGENTS.items():
        print(f"\n[{name.upper()} AGENT] Running...")
        outcome = await run_agent_safe(name, fn, state)
        test_results[name] = outcome

        if outcome["status"] == "PASS":
            r     = outcome["result"]
            score = r.get("score", "N/A")
            conf  = r.get("confidence", 0)
            flags = r.get("red_flags", [])
            print(f"  ✅ PASSED")
            print(f"     Score:      {score}")
            print(f"     Confidence: {conf:.0%}")
            print(f"     Red flags:  {len(flags)}")
            # Store output in state for meta agent
            state[OUTPUT_KEYS[name]] = r
        else:
            print(f"  ❌ FAILED")
            print(f"     Error: {outcome['error']}")
            print(f"     Traceback:\n{outcome['traceback']}")
            state[OUTPUT_KEYS[name]] = None

    # ── Meta Agent ─────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  META AGENT — Final Synthesis")
    print("=" * 60)

    passed_agents = [k for k, v in test_results.items()
                     if v["status"] == "PASS"]

    if len(passed_agents) == 0:
        print("❌ No agents passed — cannot run Meta Agent.")
    else:
        try:
            meta_result = await run_meta_agent(state)
            print(meta_result["final_explanation"])
            test_results["Meta"] = {"status": "PASS", "result": meta_result}
        except Exception as e:
            print(f"❌ Meta Agent FAILED: {e}")
            print(traceback.format_exc())
            test_results["Meta"] = {"status": "FAIL", "error": str(e)}

    # ── Final test report ──────────────────────────────────
    print("\n" + "=" * 60)
    print("  TEST REPORT")
    print("=" * 60)

    all_passed = True
    for name, outcome in test_results.items():
        status = outcome["status"]
        icon   = "✅" if status == "PASS" else "❌"
        print(f"  {icon} {name:<12} {status}")
        if status == "FAIL":
            all_passed = False
            print(f"       → {outcome.get('error', 'Unknown error')}")

    print("\n" + "=" * 60)
    if all_passed:
        print("  ✅ ALL TESTS PASSED — Pipeline is working end to end")
    else:
        failed = [k for k, v in test_results.items()
                  if v["status"] == "FAIL"]
        print(f"  ❌ FAILED: {', '.join(failed)}")
        print("  Fix the failed agents above before running full pipeline.")
    print("=" * 60)


def _fallback_financial() -> dict:
    """Used if scraper returns nothing."""
    return {
        "revenue_2023": 42000000,
        "revenue_2022": 38500000,
        "revenue_2021": 33200000,
        "net_margin": 0.084,
        "ebitda": 6300000,
        "current_ratio": 1.6,
        "quick_ratio": 1.1,
        "debt_equity_ratio": 2.1,
        "interest_coverage": 3.8,
        "duns_credit_score": 72,
        "credit_rating": "BBB",
        "top_customer_revenue": 18900000,
        "customer_count": 14,
        "years_in_operation": 11
    }


asyncio.run(main())