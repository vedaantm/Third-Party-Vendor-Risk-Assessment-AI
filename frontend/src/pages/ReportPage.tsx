import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, getReport } from "../api/client";
import { AGENT_KEYS, type AgentKey, type EvaluationReport } from "../api/types";
import AgentEvidence from "../components/AgentEvidence";
import AgentWarnings from "../components/AgentWarnings";
import OfacBanner from "../components/OfacBanner";
import RecommendationBadge from "../components/RecommendationBadge";
import RedFlagList from "../components/RedFlagList";
import ScoreSummary from "../components/ScoreSummary";
import { AGENT_LABELS, notRunAgents, truncatedAgents } from "../lib/agents";
import { formatDate } from "../lib/format";

/** The report endpoint doesn't return the OFAC result — the evaluations table doesn't store it. */
const OFAC_NOT_STORED =
  "Saved reports don't include the OFAC screening result. Check the original evaluation, or screen this vendor manually.";

function agentScores(report: EvaluationReport): Record<AgentKey, number | null> {
  return Object.fromEntries(
    AGENT_KEYS.map((k) => {
      const entry = report.agent_breakdown[k];
      return [k, entry && !("status" in entry) ? entry.score : null];
    }),
  ) as Record<AgentKey, number | null>;
}

export default function ReportPage() {
  const { evaluationId } = useParams<{ evaluationId: string }>();
  const [report, setReport] = useState<EvaluationReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!evaluationId) return;
    const controller = new AbortController();
    getReport(evaluationId, controller.signal)
      .then(setReport)
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err instanceof ApiError && err.status === 404 ? "Evaluation not found." : err instanceof Error ? err.message : "Could not load the report.");
      });
    return () => controller.abort();
  }, [evaluationId]);

  if (error) {
    return (
      <div role="alert" className="mx-auto max-w-2xl rounded-lg border border-red-200 bg-red-50 p-5 text-sm text-red-800">
        <p className="font-medium">{error}</p>
        <Link to="/history" className="mt-3 inline-block underline">Back to history</Link>
      </div>
    );
  }
  if (!report) return <p className="text-sm text-slate-500">Loading report…</p>;

  const weights = report.weights_used ?? {};

  return (
    <div className="space-y-6">
      <Link to="/history" className="text-sm text-slate-600 hover:text-slate-900">← History</Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-sm text-slate-500">Saved report · {report.evaluation_id}</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">{report.vendor_name}</h1>
          <p className="text-sm text-slate-600">
            Evaluated for {report.evaluated_for} · {formatDate(report.created_at)}
          </p>
        </div>
        <RecommendationBadge value={report.recommendation} />
      </div>

      <OfacBanner status={null} missingNote={OFAC_NOT_STORED} />
      <AgentWarnings unavailable={notRunAgents(report.agent_breakdown)} truncated={truncatedAgents(report)} />

      <ScoreSummary
        composite={report.scores.composite}
        fit={report.scores.fit}
        confidence={report.scores.confidence}
        agentScores={agentScores(report)}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <RedFlagList flags={report.red_flags} />
        <div className="card">
          <h2 className="section-title">Weights used</h2>
          <dl className="mt-3 space-y-1 text-sm">
            {AGENT_KEYS.map((k) => (
              <div key={k} className="flex justify-between">
                <dt className="text-slate-600">{AGENT_LABELS[k]}</dt>
                <dd className="tabular-nums text-slate-900">{weights[k] != null ? weights[k]!.toFixed(2) : "—"}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>

      <AgentEvidence breakdown={report.agent_breakdown} />

      {report.full_explanation && (
        <details className="card">
          <summary className="cursor-pointer text-sm font-medium text-slate-700">Full summary for the CPO</summary>
          <pre className="mt-3 whitespace-pre-wrap font-sans text-sm text-slate-700">{report.full_explanation}</pre>
        </details>
      )}
    </div>
  );
}
