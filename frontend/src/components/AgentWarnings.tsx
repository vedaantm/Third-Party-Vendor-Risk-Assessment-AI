import { agentLabel } from "../lib/agents";

interface Props {
  unavailable: string[];
  truncated: string[];
}

function names(agents: string[]) {
  return agents.map(agentLabel).join(", ");
}

/** Visible warnings for incomplete analyses — both force human review. */
export default function AgentWarnings({ unavailable, truncated }: Props) {
  if (unavailable.length === 0 && truncated.length === 0) return null;
  return (
    <div className="space-y-3">
      {unavailable.length > 0 && (
        <div role="alert" data-testid="unavailable-agents" className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-amber-900">
          <h2 className="text-sm font-semibold">
            {unavailable.length === 1 ? "1 analysis" : `${unavailable.length} analyses`} did not complete: {names(unavailable)}
          </h2>
          <p className="mt-1 text-sm">
            No usable result was produced, so its weight was spread across the other analyses. The evaluation is
            incomplete and needs human review.
          </p>
        </div>
      )}
      {truncated.length > 0 && (
        <div role="alert" data-testid="truncated-agents" className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-amber-900">
          <h2 className="text-sm font-semibold">
            Output cut off for: {names(truncated)}
          </h2>
          <p className="mt-1 text-sm">
            The score was recovered, but red flags and evidence after the cut-off may be missing. This evaluation
            needs human review.
          </p>
        </div>
      )}
    </div>
  );
}
