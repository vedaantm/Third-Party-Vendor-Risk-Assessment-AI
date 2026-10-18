import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listEvaluations } from "../api/client";
import type { EvaluationSummary } from "../api/types";
import RecommendationBadge from "../components/RecommendationBadge";
import { formatDate, formatPercent, formatScore } from "../lib/format";

const PAGE_SIZE = 50; // backend maximum is 100

export default function HistoryPage() {
  const [rows, setRows] = useState<EvaluationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    listEvaluations(PAGE_SIZE, controller.signal)
      .then(({ evaluations }) => setRows(evaluations))
      .catch((err) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Could not load evaluations.");
      });
    return () => controller.abort();
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">Evaluation history</h1>
      <p className="mt-1 text-sm text-slate-600">The {PAGE_SIZE} most recent evaluations.</p>

      {error && (
        <p role="alert" className="mt-6 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </p>
      )}
      {!error && rows === null && <p className="mt-6 text-sm text-slate-500">Loading…</p>}
      {rows && rows.length === 0 && (
        <p className="mt-6 text-sm text-slate-600">
          No evaluations yet. <Link to="/" className="underline">Evaluate a vendor</Link>.
        </p>
      )}
      {rows && rows.length > 0 && (
        <div className="mt-6 overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs text-slate-500">
              <tr>
                <th className="px-4 py-2 font-medium">Vendor</th>
                <th className="px-4 py-2 font-medium">Evaluated for</th>
                <th className="px-4 py-2 font-medium">Recommendation</th>
                <th className="px-4 py-2 text-right font-medium">Composite</th>
                <th className="px-4 py-2 text-right font-medium">Confidence</th>
                <th className="px-4 py-2 font-medium">Date</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((row) => (
                <tr key={row.evaluation_id} className="hover:bg-slate-50">
                  <td className="px-4 py-2 font-medium text-slate-900">{row.vendor_name}</td>
                  <td className="px-4 py-2 text-slate-600">{row.company_name}</td>
                  <td className="px-4 py-2">
                    <RecommendationBadge value={row.recommendation} size="sm" />
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums">{formatScore(row.scores.composite)}</td>
                  <td className="px-4 py-2 text-right tabular-nums">{formatPercent(row.confidence)}</td>
                  <td className="px-4 py-2 text-slate-600">{formatDate(row.created_at)}</td>
                  <td className="px-4 py-2 text-right">
                    <Link
                      to={`/evaluations/${row.evaluation_id}/report`}
                      className="text-sm font-medium text-slate-700 underline-offset-2 hover:underline"
                      aria-label={`Report for ${row.vendor_name}`}
                    >
                      Report
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
