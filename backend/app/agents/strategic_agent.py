from app.agents.base_agent import BaseAgent
from app.orchestrator.state import VendorRiskState

STRATEGIC_SYSTEM_PROMPT = """
You are the Strategic Fit Agent for an enterprise vendor risk platform.

Your job: evaluate how well this vendor matches the CLIENT's specific needs,
priorities, and requirements. This is NOT a general vendor assessment —
you must score fit against what THIS client needs.

SCORING CRITERIA (0-100):
- Alignment with client's stated priorities              → 35 pts
  (Score each priority match: full=7pts, partial=4pts, none=0pts)
- Vendor capability vs contract requirements             → 25 pts
  (Can they deliver the scope at the contract value/duration?)
- Dealbreaker check                                      → 20 pts
  (Any dealbreaker present = automatic -20 pts each)
- Nice-to-have alignment                                 → 10 pts
- Long-term partnership potential                        → 10 pts

RULES:
1. You MUST reference the client's specific priorities by name
2. If a dealbreaker is present, flag it explicitly
3. Score each priority match individually then aggregate
4. A vendor can be financially strong but a poor strategic fit — be honest
5. Return ONLY valid JSON

OUTPUT FORMAT:
{
  "score": <float 0-100>,
  "explanation": "<paragraph citing specific priority matches>",
  "evidence": ["<priority 1: match/partial/none>", "<priority 2: ...>"],
  "confidence": <float 0.0-1.0>,
  "red_flags": ["<flag>"],
  "priority_matches": {
    "<priority name>": "<FULL | PARTIAL | NONE>"
  },
  "dealbreakers_triggered": ["<dealbreaker>"],
  "recommendation_note": "<one line for CPO on strategic fit>"
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


class StrategicAgent(BaseAgent):

    @property
    def system_prompt(self) -> str:
        return STRATEGIC_SYSTEM_PROMPT

    @property
    def domain_data_key(self) -> str:
        return "strategic_data"

    def _build_user_prompt(
        self,
        vendor_name: str,
        domain_data: dict,
        reasoning: str
    ) -> str:
        client = domain_data.get("client_profile", {})
        priorities  = client.get("priorities", [])
        dealbreakers = client.get("dealbreakers", [])
        nice_to_have = client.get("nice_to_have", [])

        return f"""
You have already reasoned through this vendor's strategic fit.
Now produce the final strategic fit score.

Vendor: {vendor_name}
Vendor Industry: {domain_data.get('industry', 'Unknown')}
Years in Operation: {domain_data.get('years_in_operation', 'Unknown')}
Certifications: {domain_data.get('certifications', [])}

CLIENT: {client.get('company_name', 'Unknown')}
Client Industry: {client.get('industry', 'Unknown')}
Contract Value: ${client.get('contract_value', 0):,} over {client.get('contract_years', 1)} years
Risk Appetite: {client.get('risk_appetite', 'medium')}

CLIENT PRIORITIES (must match each explicitly):
{chr(10).join(f'- {p}' for p in priorities)}

DEALBREAKERS (automatic deduction if present):
{chr(10).join(f'- {d}' for d in dealbreakers)}

NICE TO HAVE:
{chr(10).join(f'- {n}' for n in nice_to_have)}

Your prior reasoning:
{reasoning}

Full vendor data:
{domain_data}

Score each priority as FULL, PARTIAL, or NONE.
Check every dealbreaker explicitly.
Return ONLY the JSON scoring object.
"""


async def run_strategic_agent(state: VendorRiskState) -> dict:
    agent = StrategicAgent()
    return await agent.run(state)