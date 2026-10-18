import type { EvaluationReport, EvaluationResult, Job } from "../api/types";

/** A realistic job result, in the exact shape routes.py returns. */
export function evaluationResult(overrides: Partial<EvaluationResult> = {}): EvaluationResult {
  return {
    evaluation_id: "eval-1a2b3c4d",
    vendor_name: "Okta",
    recommendation: "REVIEW",
    composite_score: 82.4,
    risk_score: 80.1,
    fit_score: 78.6,
    confidence: 0.88,
    dealbreaker_status: "clear",
    ofac_status: "clear",
    ofac_matches: [],
    unavailable_agents: [],
    truncated_agents: [],
    agent_scores: { financial: 84, compliance: 88, operational: 79, reputation: 76, strategic: 81 },
    red_flags: ["[REPUTATION] Data breach reported in 2023 [Reuters]"],
    final_explanation: "VENDOR EVALUATION SUMMARY — Okta\n\nRECOMMENDATION: REVIEW",
    certifications_found: ["SOC2_Type2", "FedRAMP", "ISO27001"],
    certification_evidence_tiers: {
      SOC2_Type2: "VERIFIED_REGISTRY",
      FedRAMP: "VERIFIED_REGISTRY",
      ISO27001: "CORROBORATED",
    },
    regulatory_frameworks_disclosed: ["GDPR", "CCPA"],
    cert_evidence: ["SOC2_Type2 — 10-K states SOC 2 Type II report [source: sec.gov]"],
    edgar_cik: "0001660134",
    industry: "Software",
    ...overrides,
  };
}

export function completedJob(result: Partial<EvaluationResult> = {}): Job {
  return {
    job_id: "job-1",
    vendor_name: "Okta",
    status: "complete",
    current_step: "Done",
    progress: 100,
    created_at: "2026-10-17T10:00:00+00:00",
    updated_at: "2026-10-17T10:02:30+00:00",
    result: evaluationResult(result),
  };
}

export function evaluationReport(overrides: Partial<EvaluationReport> = {}): EvaluationReport {
  return {
    evaluation_id: "eval-1a2b3c4d",
    vendor_name: "Okta",
    evaluated_for: "Acme Corp",
    created_at: "2026-10-17T10:02:30+00:00",
    recommendation: "REVIEW",
    scores: { composite: 82.4, risk: 80.1, fit: 78.6, confidence: 0.88 },
    agent_breakdown: {
      financial: {
        score: 84, confidence: 0.9, explanation: "Strong liquidity.", reasoning: "Current ratio 1.8 [Yahoo Finance].",
        evidence: ["current_ratio 1.8 [Yahoo Finance]"], red_flags: [],
      },
      compliance: {
        score: 88, confidence: 0.9, explanation: "SOC 2 Type II verified.", reasoning: "…",
        evidence: ["SOC2_Type2 [VERIFIED_REGISTRY]"], red_flags: [],
      },
      operational: { score: 79, confidence: 0.85, explanation: "Large, stable.", reasoning: "…", evidence: [], red_flags: [] },
      reputation: {
        score: 76, confidence: 0.8, explanation: "One past breach.", reasoning: "…",
        evidence: ["[Reuters] Okta discloses breach"], red_flags: ["Data breach reported in 2023 [Reuters]"],
      },
      strategic: { score: 81, confidence: 0.85, explanation: "Good fit.", reasoning: "…", evidence: [], red_flags: [] },
    },
    red_flags: ["[REPUTATION] Data breach reported in 2023 [Reuters]"],
    full_explanation: "VENDOR EVALUATION SUMMARY — Okta",
    weights_used: { financial: 0.3, compliance: 0.25, operational: 0.2, reputation: 0.1, strategic: 0.15 },
    ...overrides,
  };
}
