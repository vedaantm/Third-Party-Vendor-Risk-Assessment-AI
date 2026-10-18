import { AGENT_KEYS, type AgentKey } from "../api/types";
import { AGENT_LABELS } from "../lib/agents";
import { formatPercent, formatScore } from "../lib/format";

interface Props {
  composite: number | null | undefined;
  fit: number | null | undefined;
  confidence: number | null | undefined;
  agentScores: Partial<Record<AgentKey, number | null>>;
}

function Stat({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{value}</dd>
      <p className="mt-0.5 text-xs text-slate-500">{hint}</p>
    </div>
  );
}

export default function ScoreSummary({ composite, fit, confidence, agentScores }: Props) {
  return (
    <div className="space-y-4">
      <dl className="grid gap-3 sm:grid-cols-3">
        <Stat label="Composite" value={formatScore(composite)} hint="Weighted across all analyses, out of 100" />
        <Stat label="Fit score" value={formatScore(fit)} hint="Strategic, operational and reputation, out of 100" />
        <Stat label="Confidence" value={formatPercent(confidence)} hint="Auto-approval requires 95%" />
      </dl>
      <div className="card">
        <h2 className="section-title">Analysis scores</h2>
        <ul className="mt-3 space-y-3" aria-label="Agent scores">
          {AGENT_KEYS.map((key) => {
            const score = agentScores[key];
            const available = score != null && Number.isFinite(score);
            return (
              <li key={key} className="grid grid-cols-[8rem_1fr_3.5rem] items-center gap-3 text-sm">
                <span className="text-slate-700">{AGENT_LABELS[key]}</span>
                <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                  {available && <div className="h-full rounded-full bg-slate-600" style={{ width: `${Math.max(0, Math.min(100, score))}%` }} />}
                </div>
                <span className={`text-right tabular-nums ${available ? "text-slate-900" : "text-amber-700"}`}>
                  {available ? formatScore(score) : "N/A"}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
