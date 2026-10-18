import { AGENT_KEYS, type AgentBreakdownEntry, type AgentKey } from "../api/types";
import { AGENT_LABELS } from "../lib/agents";
import { formatPercent, formatScore } from "../lib/format";

interface Props {
  breakdown: Record<AgentKey, AgentBreakdownEntry> | null | undefined;
  certEvidence?: string[] | null;
  /** Shown instead of the per-agent section, e.g. while the report loads. */
  breakdownNote?: string | null;
}

function EvidenceItems({ items }: { items: string[] }) {
  if (items.length === 0) return <p className="text-sm text-slate-500">No evidence cited.</p>;
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm text-slate-700">
      {items.map((e, i) => (
        <li key={`${i}-${e}`}>{e}</li>
      ))}
    </ul>
  );
}

export default function AgentEvidence({ breakdown, certEvidence, breakdownNote }: Props) {
  return (
    <div className="card space-y-5">
      <h2 className="section-title">Evidence</h2>

      {certEvidence && certEvidence.length > 0 && (
        <div>
          <h3 className="mb-1 text-sm font-medium text-slate-800">Certification discovery</h3>
          <EvidenceItems items={certEvidence} />
        </div>
      )}

      {breakdownNote ? (
        <p className="text-sm text-slate-500">{breakdownNote}</p>
      ) : (
        breakdown &&
        AGENT_KEYS.map((key) => {
          const entry = breakdown[key];
          if (!entry || "status" in entry) {
            return (
              <div key={key}>
                <h3 className="text-sm font-medium text-slate-800">{AGENT_LABELS[key]}</h3>
                <p className="text-sm text-amber-700">Not run — no result from this analysis.</p>
              </div>
            );
          }
          return (
            <div key={key}>
              <h3 className="flex items-baseline gap-2 text-sm font-medium text-slate-800">
                {AGENT_LABELS[key]}
                <span className="text-xs font-normal tabular-nums text-slate-500">
                  {formatScore(entry.score)} · confidence {formatPercent(entry.confidence)}
                </span>
              </h3>
              {entry.explanation && <p className="mb-1 mt-0.5 text-sm text-slate-600">{entry.explanation}</p>}
              <EvidenceItems items={entry.evidence ?? []} />
              {entry.reasoning && (
                <details className="mt-2 text-sm">
                  <summary className="cursor-pointer text-slate-600 hover:text-slate-900">Analyst reasoning</summary>
                  <p className="mt-1 whitespace-pre-wrap rounded bg-slate-50 p-3 text-slate-700">{entry.reasoning}</p>
                </details>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}
