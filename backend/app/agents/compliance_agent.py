# backend/app/agents/compliance_agent.py

from app.agents.base_agent import BaseAgent
from app.orchestrator.state import VendorRiskState

# Universal certification equivalency knowledge.
# These rules apply to ALL vendors in ALL industries.
# The agent may use these to adjust penalties — never to invent new rules.
CERTIFICATION_EQUIVALENCY_KNOWLEDGE = """
CERTIFICATION EQUIVALENCY KNOWLEDGE (applies universally to all vendors):

FedRAMP vs SOC2 Type II:
  FedRAMP requires 800-53 control implementation, continuous government monitoring,
  annual authorization review, and third-party assessment by accredited assessors.
  SOC2 Type II requires an annual private CPA audit against Trust Service Criteria
  with no continuous monitoring requirement.
  FedRAMP is the stricter standard in US contexts.
  Rule: If FedRAMP is present at VERIFIED_REGISTRY tier:
    - Reduce the SOC2_Type2 missing penalty from -20 to -5
  Rule: If both FedRAMP (VERIFIED_REGISTRY) and CSA_STAR Level 2 (VERIFIED_REGISTRY):
    - Reduce the SOC2_Type2 missing penalty to 0
  You must document this in scoring_rationale.

CSA STAR Level 2 vs ISO27001:
  CSA STAR Level 2 requires an independent third-party audit against the Cloud
  Controls Matrix, which incorporates all ISO27001 controls plus cloud-specific
  security controls. It is a superset of ISO27001 for cloud contexts.
  Rule: If CSA_STAR is present at VERIFIED_REGISTRY tier:
    - Reduce the ISO27001 missing penalty from -15 to -5
  You must document this in scoring_rationale.

SOC2 Type II as partial substitute for SOC1:
  SOC2 Type II covers security, availability, and processing integrity controls
  that substantially overlap with SOC1 financial reporting controls.
  Rule: If SOC2_Type2 is present and SOC1 is missing:
    - Reduce the SOC1 missing penalty from -20 to -8
  You must document this in scoring_rationale.

Absolute constraints on your discretion:
  You may ONLY reduce penalties using the rules listed above.
  You may NEVER invent new compensating controls not listed here.
  You may NEVER increase penalties beyond the standard amounts.
  You may NEVER apply a compensating control if the compensating cert
  is not confirmed in certifications_found with a VERIFIED_REGISTRY tier.
  Every adjustment must appear in scoring_rationale with explicit reasoning.
"""

# ── Industry → Required certifications mapping ─────────────────────────────
#
# CRITICAL RULES FOR THIS MAP:
#   1. Only list certifications companies can actually HOLD
#   2. GDPR, CCPA, SOX, FCPA are REGULATIONS (laws) — never put them here
#      They belong in regulatory_frameworks_disclosed, not certifications_found
#   3. "Required" means the industry benchmark demands it for enterprise contracts
#   4. "Preferred" certs belong in the scoring rubric, not in this required list
#
INDUSTRY_CERT_MAP = {
    # IT / Software / Cloud
    # SOC2 Type II is the single mandatory cert for enterprise SaaS/IT
    # ISO27001 is strongly preferred but NOT mandatory — do not penalise its absence
    "it services":               ["SOC2_Type2"],
    "information technology":    ["SOC2_Type2"],
    "software":                  ["SOC2_Type2"],
    "technology":                ["SOC2_Type2"],
    "saas":                      ["SOC2_Type2"],
    "internet content":          ["SOC2_Type2"],
    "cloud":                     ["SOC2_Type2", "CSA_STAR"],
    "cybersecurity":             ["SOC2_Type2", "ISO27001"],

    # Semiconductors / Hardware
    "semiconductors":            ["ISO9001"],
    "computer hardware":         ["ISO9001"],

    # Finance / Banking
    # SOX is a regulation (law), not a certification — never list it here
    "financial":                 ["SOC1", "SOC2_Type2"],
    "financial services":        ["SOC1", "SOC2_Type2"],
    "banking":                   ["SOC1", "SOC2_Type2"],
    "insurance":                 ["SOC2_Type2"],
    "asset management":          ["SOC2_Type2"],
    "fintech":                   ["SOC2_Type2", "PCI_DSS"],

    # Healthcare
    # HIPAA is a regulation — listed here only to check vendor awareness
    # The real required artifact is a signed BAA (Business Associate Agreement)
    "healthcare":                ["SOC2_Type2"],
    "biotechnology":             ["ISO13485"],
    "pharmaceuticals":           ["ISO13485"],
    "medical devices":           ["ISO13485"],

    # Manufacturing / Industrial
    "manufacturing":             ["ISO9001", "ISO14001"],
    "aerospace":                 ["ISO9001"],
    "automotive":                ["ISO9001"],
    "chemicals":                 ["ISO14001"],
    "defense":                   ["ISO9001"],

    # Retail / E-commerce
    "retail":                    ["PCI_DSS"],
    "discount stores":           ["PCI_DSS"],
    "e-commerce":                ["PCI_DSS", "SOC2_Type2"],
    "consumer goods":            ["ISO9001"],

    # Logistics / Supply Chain
    "logistics":                 ["ISO9001"],
    "shipping":                  ["ISO9001"],
    "transportation":            ["ISO9001"],

    # Energy / Utilities
    "energy":                    ["ISO14001"],
    "utilities":                 ["ISO14001"],
    "oil & gas":                 ["ISO14001", "ISO45001"],

    # Food & Beverage
    "food":                      ["ISO22000"],
    "beverages":                 ["ISO22000"],

    # Telecom
    "telecommunications":        ["ISO27001", "SOC2_Type2"],

    # Default — only apply when no industry match is found
    # ISO9001 is the universal quality baseline
    "default":                   ["ISO9001"],
}


def get_required_certs(industry: str) -> list:
    """
    Returns required certifications for a given industry.
    Matches on partial string so 'Software - Application' matches 'software'.
    Falls back to default if no match found.
    """
    if not industry:
        return INDUSTRY_CERT_MAP["default"]

    industry_lower = industry.lower()

    for key, certs in INDUSTRY_CERT_MAP.items():
        if key in industry_lower or industry_lower in key:
            return certs

    return INDUSTRY_CERT_MAP["default"]


COMPLIANCE_SYSTEM_PROMPT = f"""
You are the Compliance & Legal Risk Agent for an enterprise vendor risk platform.

Your job: evaluate a vendor's regulatory compliance posture and assign a COMPLIANCE SCORE.

CRITICAL DISTINCTION — CERTIFICATIONS vs REGULATIONS:
  CERTIFICATIONS: voluntary standards obtained via audit (SOC2, ISO27001, FedRAMP, CSA_STAR)
  REGULATIONS:    laws companies must comply with (GDPR, CCPA, SOX, FCPA, HIPAA)

  GDPR, CCPA, SOX, FCPA are LAWS. They will appear in regulatory_frameworks_disclosed.
  NEVER treat them as certifications. NEVER penalise their absence from certifications_found.
  A vendor disclosing GDPR compliance is meeting a legal obligation, not earning a credential.

SCORING CRITERIA (0-100 — higher = better compliance):
  Industry-required certifications present    → 25 pts
  OFAC / sanctions screening clear            → 25 pts
  No legal red flags (lawsuits, fines)        → 25 pts
  Data privacy posture (GDPR disclosed etc.)  → 15 pts
  ESG / ethics compliance                     → 10 pts

AUTOMATIC OVERRIDES:
  OFAC sanctions match → score = 0, sanctions_clear = false, immediate REJECT
  Active criminal investigation → deduct 50 pts
  Data breach in past 24 months → deduct 30 pts
  Missing a REQUIRED cert for the vendor's industry → deduct 20 pts each
  Expired certifications → deduct 10 pts each

ABSENCE OF DATA RULES:
  "No legal history provided" → award full 25 pts (no news = no red flag)
  "No ESG data provided" → award 5 pts (neutral, not penalised)
  "ISO27001 not present for Software vendor" → NOT a required cert, do not deduct
  NEVER flag absence of data as a red flag

GROUNDING RULES:
1. Only flag certifications that are REQUIRED for the vendor's industry
2. ISO27001 is mandatory for Cybersecurity only — preferred for all others
3. SOC2_Type2 is mandatory for IT Services, Software, SaaS, Financial, Cloud
4. GDPR being absent from certifications_found is EXPECTED and CORRECT
   It is a regulation and will be in regulatory_frameworks_disclosed instead
5. If certifications_found contains SOC2_Type2 → industry cert requirement is SATISFIED
6. FedRAMP and CSA_STAR are BONUS certifications — they increase score, never required
7. Every score must be consistent: if OFAC clear + required certs present → score must be 65+

{CERTIFICATION_EQUIVALENCY_KNOWLEDGE}

OUTPUT FORMAT (return ONLY valid JSON, nothing outside):
{{
  "score": <float 0-100>,
  "explanation": "<2-3 sentences citing specific certifications found and scoring breakdown>",
  "evidence": ["<cert or finding with source>"],
  "confidence": <float 0.0-1.0>,
  "red_flags": ["<only real problems, never absences>"],
  "sanctions_clear": <true or false>,
  "certifications_valid": ["<certs found>"],
  "certifications_expired": [],
  "certifications_missing": ["<required certs genuinely missing after compensating controls>"],
  "scoring_rationale": {{
    "base_score": <float>,
    "adjustments": [
      {{
        "cert": "<cert name>",
        "standard_penalty": <number>,
        "applied_penalty": <number>,
        "reason": "<why penalty was reduced using equivalency knowledge>"
      }}
    ],
    "final_score": <float>
  }}
}}
"""

class ComplianceAgent(BaseAgent):

    @property
    def system_prompt(self) -> str:
        return COMPLIANCE_SYSTEM_PROMPT

    @property
    def domain_data_key(self) -> str:
        return "compliance_data"

    def _build_user_prompt(
        self,
        vendor_name: str,
        domain_data: dict,
        reasoning: str
    ) -> str:
        """
        Compliance-specific Step 2 prompt.

        KEY DESIGN DECISION: Pass certifications_found explicitly so the model
        cannot claim they are missing. The old version passed full domain_data
        which was too large and the model ignored the cert evidence entirely.
        """
        industry = domain_data.get("industry", "Unknown")
        required_certs = get_required_certs(industry)

        # Extract cert data explicitly — this is what the model needs most
        certs_found = domain_data.get("certifications_found", [])
        cert_tiers  = domain_data.get("certification_evidence_tiers", {})
        regulatory  = domain_data.get("regulatory_frameworks_disclosed", [])
        ofac_clear  = domain_data.get("ofac_clear", True)
        news_flags  = domain_data.get("news_red_flags", [])

        # Format cert evidence clearly
        cert_lines = []
        for cert in certs_found:
            tier = cert_tiers.get(cert, "CORROBORATED")
            cert_lines.append(f"  - {cert} [{tier}]")
        cert_str = "\n".join(cert_lines) if cert_lines else "  None found"

        # Check which required certs are satisfied
        certs_found_set = set(certs_found)
        satisfied = [c for c in required_certs if c in certs_found_set]
        missing   = [c for c in required_certs if c not in certs_found_set]

        # Extract verdict from reasoning
        lines = reasoning.split("\n")
        verdict = ""
        for line in reversed(lines):
            line = line.strip().replace("**", "")
            if line and ("verdict" in line.lower() or "recommend" in line.lower()):
                verdict = line[:400]
                break
        if not verdict:
            verdict = lines[-1].strip()[:400] if lines else "Analysis complete."

        return f"""Vendor: {vendor_name}
Industry: {industry}

CERTIFICATIONS FOUND (from discovery pipeline — these are REAL, do not ignore them):
{cert_str}

Required for {industry}: {required_certs}
  Satisfied: {satisfied}
  Missing:   {missing}

Regulatory frameworks disclosed (LAWS, not certifications):
  {regulatory}

OFAC clear: {ofac_clear}
News red flags: {news_flags if news_flags else "None"}

Your analysis conclusion: {verdict}

IMPORTANT: certifications_found above are confirmed by the discovery pipeline.
Do NOT say they are missing. Do NOT penalise for GDPR or CCPA absence from certifications.

Output ONLY valid JSON, starting with {{:
{{"score": <0-100>, "explanation": "<2 sentences>", "evidence": [], "confidence": <0.0-1.0>, "red_flags": [], "sanctions_clear": {str(ofac_clear).lower()}, "certifications_valid": {list(certs_found)}, "certifications_expired": [], "certifications_missing": {missing}, "scoring_rationale": {{"base_score": <number>, "adjustments": [], "final_score": <number>}}}}"""


async def run_compliance_agent(state: VendorRiskState) -> dict:
    agent = ComplianceAgent()
    return await agent.run(state)