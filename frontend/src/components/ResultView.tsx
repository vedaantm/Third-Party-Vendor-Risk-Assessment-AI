import { Link } from "react-router-dom";
import type { EvaluationReport, EvaluationResult } from "../api/types";
import { truncatedAgentsFromRedFlags } from "../lib/agents";
import AgentEvidence from "./AgentEvidence";
import AgentWarnings from "./AgentWarnings";
import CertificationTiers from "./CertificationTiers";
import OfacBanner from "./OfacBanner";
import RecommendationBadge from "./RecommendationBadge";
import RedFlagList from "./RedFlagList";
import ScoreSummary from "./ScoreSummary";

const DEALBREAKER_TEXT: Record<string, string> = {
  clear: "No dealbreakers triggered.",
  needs_review: "A dealbreaker is only self-reported by the vendor — needs review.",
  hard_reject: "A dealbreaker was triggered with no evidence clearing it.",
};

interface Props {
  result: EvaluationResult;
  report: EvaluationReport | null;
  reportError: string | null;
}

export default function ResultView({ result, report, reportError }: Props) {
  // Older stored results predate the field; only they fall back to red flags
  const truncated = result.truncated_agents ?? truncatedAgentsFromRedFlags(result.red_flags);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-sm text-slate-500">Evaluation {result.evaluation_id}</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">{result.vendor_name}</h1>
          {result.industry && <p className="text-sm text-slate-600">{result.industry}</p>}
        </div>
        <div className="text-right">
          <RecommendationBadge value={result.recommendation} />
          {result.dealbreaker_status && (
            <p className="mt-2 text-xs text-slate-600">{DEALBREAKER_TEXT[result.dealbreaker_status] ?? result.dealbreaker_status}</p>
          )}
        </div>
      </div>

      <OfacBanner status={result.ofac_status} matches={result.ofac_matches} />
      <AgentWarnings unavailable={result.unavailable_agents ?? []} truncated={truncated} />

      <ScoreSummary
        composite={result.composite_score}
        fit={result.fit_score}
        confidence={result.confidence}
        agentScores={result.agent_scores}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <RedFlagList flags={result.red_flags} />
        <CertificationTiers tiers={result.certification_evidence_tiers} regulations={result.regulatory_frameworks_disclosed} />
      </div>

      <AgentEvidence
        breakdown={report?.agent_breakdown}
        certEvidence={result.cert_evidence}
        breakdownNote={reportError ?? (report ? null : "Loading each analysis's evidence…")}
      />

      {result.final_explanation && (
        <details className="card">
          <summary className="cursor-pointer text-sm font-medium text-slate-700">Full summary for the CPO</summary>
          <pre className="mt-3 whitespace-pre-wrap font-sans text-sm text-slate-700">{result.final_explanation}</pre>
        </details>
      )}

      <div className="flex gap-3">
        <Link to="/" className="btn-secondary">New evaluation</Link>
        <Link to={`/evaluations/${result.evaluation_id}/report`} className="btn-secondary">Saved report</Link>
      </div>
    </div>
  );
}
