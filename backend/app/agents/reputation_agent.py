import json
from app.agents.base_agent import BaseAgent
from app.orchestrator.state import VendorRiskState

REPUTATION_SYSTEM_PROMPT = """
You are the Reputation Agent for an enterprise vendor risk platform.

Your job: evaluate the vendor's public reputation based on real news,
reviews, and public signals.

SCORING CRITERIA (0-100):
- Sentiment of recent news coverage                  → 30 pts
  (positive=30, neutral=20, mixed=10, negative=0)
- Absence of scandals, controversies, legal issues   → 30 pts
- Customer satisfaction signals                      → 20 pts
- Industry standing and recognition                  → 20 pts

AUTOMATIC RED FLAGS:
- Criminal charges in news → deduct 40 points
- Data breach mentioned → deduct 30 points
- Mass layoffs or leadership scandal → deduct 20 points
- Regulatory fine or SEC/FTC action → deduct 15 points
- Negative customer reviews pattern → deduct 15 points

RULES:
1. You MUST reference specific news headlines or snippets provided
2. Do not invent news — only use what is in the data
3. If no news is available, state this and lower confidence
4. Distinguish between old news (>1 year) and recent news (<6 months)
5. Return ONLY valid JSON

OUTPUT FORMAT:
{
  "score": <float 0-100>,
  "explanation": "<paragraph citing specific news items>",
  "evidence": ["<news item 1>", "<news item 2>"],
  "confidence": <float 0.0-1.0>,
  "red_flags": ["<flag>"],
  "news_sentiment": "<POSITIVE | NEUTRAL | MIXED | NEGATIVE>",
  "key_news_items": ["<headline 1>", "<headline 2>"]
}

CRITICAL GROUNDING RULES — VIOLATIONS WILL CORRUPT THE EVALUATION:

1. Red flags require EXPLICIT evidence, never absence of data.
   ❌ VIOLATION: "Penetration testing reports are missing" — absence is not evidence.
   ❌ VIOLATION: "Limited information available" — not a risk.
   ✅ CORRECT:   "Data breach reported [source: Reuters 2024]"

2. NEVER confuse benchmark thresholds with vendor values.
   ❌ VIOLATION: "Debt-to-equity of 1.5 exceeds threshold" when actual value is 0.10.
   ❌ VIOLATION: "EBITDA margin of 18-25% is concerning" — that's the benchmark range, not vendor data.
   ✅ CORRECT:   "Debt-to-equity of 2.3 [Yahoo Finance] exceeds threshold of 1.5"
   RULE: If vendor value < threshold → vendor PASSES → no red flag.

3. NEVER cite documents not in the provided data.
   ❌ VIOLATION: "[1] Tata Consultancy Services Annual Report 2022" — never provided.
   ❌ VIOLATION: "vendor's website" unless a URL was given to you.
   ✅ CORRECT:   Only cite sources that appear in the data you received.

4. NEVER use training knowledge about the company.
   All facts must come from the provided data fields, not what you know
   about the company from your training.

5. If a metric is not in the provided data, say "not provided" and move on.
   Do not estimate, infer, or substitute a typical industry value.

6. A high score from another agent means that domain is healthy.
   Do not contradict it with invented concerns.
7. Financial ratios are already formatted — use these values directly:
   - net_margin, gross_margin, revenue_growth are in PERCENT
     (e.g. 13.9 means 13.9% — compare directly to benchmarks like 8% or 12%)
   - debt_equity_ratio is a decimal ratio already divided
     (e.g. 0.1021 means 10.21% leverage — acceptable threshold is 1.5)
   - current_ratio and quick_ratio are raw ratios (healthy above 1.5)
   - interest_coverage is a raw multiple (investment grade above 5x)
   Never convert or multiply these values — they are correct as given.
   13.9% revenue growth EXCEEDS an 8% benchmark — never say it is "low"
   or "below" a lower threshold.
"""


class ReputationAgent(BaseAgent):

    @property
    def system_prompt(self) -> str:
        return REPUTATION_SYSTEM_PROMPT

    @property
    def domain_data_key(self) -> str:
        return "reputation_data"

    def _build_user_prompt(
        self,
        vendor_name: str,
        domain_data: dict,
        reasoning: str
    ) -> str:
        news    = domain_data.get("recent_news", [])
        flags   = domain_data.get("news_red_flags", [])
        certs   = domain_data.get("certifications", [])

        news_text = "\n".join([
            f"- [{a.get('source', '')}] {a.get('title', '')} | "
            f"{a.get('snippet', '')}"
            for a in news
        ]) if news else "No news articles found."

        return f"""
You have already reasoned through this vendor's reputation data.
Now produce the final reputation score.

Vendor: {vendor_name}
Industry: {domain_data.get('industry', 'Unknown')}
Years in Operation: {domain_data.get('years_in_operation', 'Unknown')}
Certifications: {certs}
Automated Red Flag Signals: {flags}

RECENT NEWS ARTICLES:
{news_text}

Your prior reasoning:
{reasoning}

Analyze the news articles above carefully.
Reference specific headlines in your evidence.
Return ONLY the JSON scoring object.
"""


async def run_reputation_agent(state: VendorRiskState) -> dict:
    # Pass news into reputation_data from compliance_data
    compliance = state.get("compliance_output") or {}
    rep_data   = state.get("reputation_data", {})
    rep_data["recent_news"] = state.get(
        "compliance_data", {}
    ).get("recent_news", [])
    state["reputation_data"] = rep_data

    agent = ReputationAgent()
    return await agent.run(state)