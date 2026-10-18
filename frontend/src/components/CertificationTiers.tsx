const TIERS: Record<string, { label: string; className: string; description: string }> = {
  VERIFIED_REGISTRY: {
    label: "Verified",
    className: "border-slate-800 bg-slate-800 text-white",
    description: "Registry or SEC filing",
  },
  CORROBORATED: {
    label: "Corroborated",
    className: "border-slate-300 bg-slate-100 text-slate-800",
    description: "Vendor's own published sources",
  },
  SELF_REPORTED_UNVERIFIABLE: {
    label: "Self-reported",
    className: "border-amber-300 bg-amber-50 text-amber-800",
    description: "No independent source",
  },
  CONTRADICTED: {
    label: "Contradicted",
    className: "border-red-300 bg-red-50 text-red-800",
    description: "Evidence conflicts",
  },
};

interface Props {
  tiers: Record<string, string> | null | undefined;
  regulations: string[] | null | undefined;
}

export default function CertificationTiers({ tiers, regulations }: Props) {
  const entries = Object.entries(tiers ?? {});
  const regs = regulations ?? [];
  return (
    <div className="card">
      <h2 className="section-title">Certifications and evidence tier</h2>
      {entries.length === 0 ? (
        <p className="mt-2 text-sm text-slate-600">No certifications found.</p>
      ) : (
        <table className="mt-3 w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-500">
              <th className="pb-2 font-medium">Certification</th>
              <th className="pb-2 font-medium">Evidence</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {entries.map(([cert, tier]) => {
              const info = TIERS[tier];
              return (
                <tr key={cert}>
                  <td className="py-2 font-mono text-xs text-slate-800">{cert}</td>
                  <td className="py-2">
                    <span
                      className={`inline-block rounded border px-2 py-0.5 text-xs font-medium ${
                        info?.className ?? "border-slate-300 bg-white text-slate-700"
                      }`}
                      title={info?.description}
                    >
                      {info?.label ?? tier}
                    </span>
                    {info && <span className="ml-2 text-xs text-slate-500">{info.description}</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {regs.length > 0 && (
        <p className="mt-4 text-xs text-slate-600">
          <span className="font-medium">Regulations disclosed</span> (laws, not certifications): {regs.join(", ")}
        </p>
      )}
    </div>
  );
}
