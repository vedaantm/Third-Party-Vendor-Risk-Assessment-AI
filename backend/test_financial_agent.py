import asyncio
import json
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=r"C:\Users\dell\Desktop\IBM WATSON\backend\.env")

from app.agents.financial_agent import run_financial_agent
from app.services.scraper import scrape_vendor_financials

# ── User only needs to provide this ───────────────────────
VENDOR_NAME = "Walmart"   # ← change this to any company
# ──────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("  VENDOR RISK PLATFORM — Financial Agent")
    print("=" * 60)

    # Auto-scrape financial data
    financial_data = scrape_vendor_financials(VENDOR_NAME)

    if not financial_data:
        print("⚠️  No data scraped — using manual fallback data")
        financial_data = {
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

    print(f"\n  Scraped data:")
    print(json.dumps(financial_data, indent=2))

    test_state = {
        "vendor_id":           "v001",
        "vendor_name":         VENDOR_NAME,
        "company_id":          "acme-001",
        "evaluation_id":       "eval-test-001",
        "weight_financial":    0.30,
        "weight_compliance":   0.25,
        "weight_operational":  0.20,
        "weight_reputation":   0.10,
        "weight_strategic":    0.15,
        "financial_data":      financial_data,
        "compliance_data":     {},
        "operational_data":    {},
        "reputation_data":     {},
        "strategic_data":      {},
        "pipeline_status":     "running",
        "conflict_detected":   False,
        "data_complete":       True,
        "missing_fields":      []
    }

    result = await run_financial_agent(test_state)

    print("\n" + "=" * 60)
    print("  FINAL RESULT")
    print("=" * 60)
    print(json.dumps(result, indent=2))

    score      = result.get("score", 0)
    confidence = result.get("confidence", 0)

    print(f"\nScore:      {score}")
    print(f"Confidence: {confidence}")
    print("✅ Score in range" if 60 <= score <= 75
          else f"ℹ️  Score: {score}")


asyncio.run(main())