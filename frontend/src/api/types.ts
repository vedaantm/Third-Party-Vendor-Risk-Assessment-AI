// Types mirror the backend exactly — see backend/app/api/routes.py and
// backend/app/db/models.py. Fields the backend may send as null are typed
// as such; the UI must never treat a missing value as a "good" one.

export const AGENT_KEYS = ["financial", "compliance", "operational", "reputation", "strategic"] as const;
export type AgentKey = (typeof AGENT_KEYS)[number];

export type Weights = Record<AgentKey, number>;

export type Recommendation = "APPROVE" | "REVIEW" | "REJECT";

/** app/services/ofac_screening.py — null when absent (e.g. saved reports). */
export type OfacStatus = "clear" | "match" | "possible_match" | "unavailable";

export type EvidenceTier =
  | "VERIFIED_REGISTRY"
  | "CORROBORATED"
  | "SELF_REPORTED_UNVERIFIABLE"
  | "CONTRADICTED";

// ── POST /api/v1/evaluate ─────────────────────────────────

/** routes.ClientProfile — every field is required by the backend. */
export interface ClientProfile {
  company_name: string;
  industry: string;
  contract_value: number;
  contract_years: number;
  risk_appetite: string;
  priorities: string[];
  dealbreakers: string[];
  nice_to_have: string[];
}

/** routes.EvaluateRequest — weights must name all five agents and sum to 1. */
export interface EvaluateRequest {
  vendor_name: string;
  client_profile: ClientProfile;
  weights: Weights;
}

/** 202 Accepted body. */
export interface EvaluateAccepted {
  job_id: string;
  evaluation_id: string;
  vendor_name: string;
  status: "pending";
  message: string;
  poll_url: string;
}

/** FastAPI / pydantic 422 detail entry. */
export interface ValidationErrorItem {
  type: string;
  loc: (string | number)[];
  msg: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

// ── GET /api/v1/jobs/{id} ─────────────────────────────────

export type JobStatus = "pending" | "running" | "complete" | "failed";

/** Job.to_status_dict (+ result when complete, + error when failed). */
export interface Job {
  job_id: string;
  vendor_name: string;
  status: JobStatus;
  current_step: string | null;
  progress: number;
  created_at: string;
  updated_at: string;
  result?: EvaluationResult | null;
  error?: string | null;
}

/** response_data built at the end of routes._run_evaluation_pipeline. */
export interface EvaluationResult {
  evaluation_id: string;
  vendor_name: string;
  recommendation: Recommendation | null;
  composite_score: number | null;
  risk_score: number | null;
  fit_score: number | null;
  confidence: number | null;
  dealbreaker_status: "clear" | "needs_review" | "hard_reject" | null;
  ofac_status: OfacStatus | null;
  ofac_matches: string[];
  unavailable_agents: string[];
  /**
   * Agents whose output was truncated (forces REVIEW). Absent only on job
   * results stored before routes.py started returning it.
   */
  truncated_agents?: string[];
  agent_scores: Record<AgentKey, number | null>;
  red_flags: string[];
  final_explanation: string | null;
  certifications_found: string[];
  certification_evidence_tiers: Record<string, EvidenceTier | string>;
  regulatory_frameworks_disclosed: string[];
  cert_evidence: string[];
  edgar_cik: string | null;
  industry: string | null;
}

// ── GET /api/v1/companies/search ──────────────────────────

export interface CompanyMatch {
  name: string;
  ticker: string;
  cik: string;
}

// ── GET /api/v1/evaluations ───────────────────────────────

/** Evaluation.to_dict */
export interface EvaluationSummary {
  id: string;
  vendor_name: string;
  company_name: string;
  evaluation_id: string;
  scores: Record<AgentKey | "composite" | "risk" | "fit", number | null>;
  confidence: number | null;
  recommendation: Recommendation | null;
  red_flags: string[] | null;
  created_at: string;
}

// ── GET /api/v1/evaluations/{id}/report ───────────────────

export interface AgentReport {
  score: number | null;
  confidence: number | null;
  explanation: string | null;
  reasoning: string | null;
  evidence: string[];
  red_flags: string[];
}

export type AgentBreakdownEntry = AgentReport | { status: "not run" };

export interface EvaluationReport {
  evaluation_id: string;
  vendor_name: string;
  evaluated_for: string;
  created_at: string;
  recommendation: Recommendation | null;
  scores: {
    composite: number | null;
    risk: number | null;
    fit: number | null;
    confidence: number | null;
  };
  agent_breakdown: Record<AgentKey, AgentBreakdownEntry>;
  red_flags: string[] | null;
  full_explanation: string | null;
  weights_used: Partial<Weights> | null;
}
