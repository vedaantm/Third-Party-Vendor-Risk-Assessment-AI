import { agentLabel, splitRedFlag } from "../lib/agents";

export default function RedFlagList({ flags }: { flags: string[] | null | undefined }) {
  const list = flags ?? [];
  return (
    <div className="card">
      <h2 className="section-title">Red flags ({list.length})</h2>
      {list.length === 0 ? (
        <p className="mt-2 text-sm text-slate-600">None reported.</p>
      ) : (
        <ul className="mt-3 divide-y divide-slate-100" aria-label="Red flags">
          {list.map((flag, i) => {
            const { agent, text } = splitRedFlag(flag);
            return (
              <li key={`${i}-${flag}`} className="flex gap-3 py-2 text-sm">
                {agent && (
                  <span className="mt-0.5 h-fit shrink-0 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-xs text-slate-600">
                    {agentLabel(agent)}
                  </span>
                )}
                <span className="text-slate-800">{text}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
