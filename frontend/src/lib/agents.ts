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
 * Agents whose output was truncated, derived from the truncation red flag
 * the meta-agent always surfaces. Only for data that lacks the
 * truncated_agents field: saved reports, and job results stored before
 * routes.py started returning it. Prefer the field when it's present.
 */
export function truncatedAgentsFromRedFlags(redFlags: string[] | null | undefined): string[] {
  const found = new Set<string>();
  for (const flag of redFlags ?? []) {
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
