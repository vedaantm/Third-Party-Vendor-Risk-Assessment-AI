import { AGENT_KEYS, type AgentBreakdownEntry, type AgentKey } from "../api/types";

export const AGENT_LABELS: Record<AgentKey, string> = {
  financial: "Financial",
  compliance: "Compliance",
  operational: "Operational",
  reputation: "Reputation",
  strategic: "Strategic fit",
};

export function agentLabel(name: string): string {
  return (AGENT_LABELS as Record<string, string>)[name.toLowerCase()] ?? name;
}

// meta_agent.py prefixes every red flag with "[AGENT] "; base_agent.py adds
// this flag to any output salvaged from truncated JSON.
const TRUNCATION_FLAG = /^\[([A-Z]+)\] Agent output was truncated/;

/**
 * Agents whose output was truncated. Uses `truncated_agents` when the
 * backend provides it; the job result from routes.py doesn't include it
 * today, so otherwise it's derived from the truncation red flag that the
 * meta-agent always surfaces.
 */
export function truncatedAgents(source: { truncated_agents?: string[] | null; red_flags?: string[] | null }): string[] {
  if (Array.isArray(source.truncated_agents)) return [...source.truncated_agents].sort();
  const found = new Set<string>();
  for (const flag of source.red_flags ?? []) {
    const m = TRUNCATION_FLAG.exec(flag);
    if (m) found.add(m[1]!.toLowerCase());
  }
  return [...found].sort();
}

/** Agents a saved report marks as "not run". */
export function notRunAgents(breakdown: Record<AgentKey, AgentBreakdownEntry> | null | undefined): AgentKey[] {
  if (!breakdown) return [];
  return AGENT_KEYS.filter((k) => {
    const entry = breakdown[k];
    return !entry || "status" in entry;
  });
}

/** "[REPUTATION] Data breach…" → { agent: "reputation", text: "Data breach…" } */
export function splitRedFlag(flag: string): { agent: string | null; text: string } {
  const m = /^\[([A-Z]+)\]\s*(.*)$/s.exec(flag);
  return m ? { agent: m[1]!.toLowerCase(), text: m[2]! } : { agent: null, text: flag };
}
