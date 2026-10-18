import type { Recommendation } from "../api/types";

const STYLES: Record<Recommendation, string> = {
  APPROVE: "border-emerald-300 bg-emerald-50 text-emerald-800",
  REVIEW: "border-amber-300 bg-amber-50 text-amber-800",
  REJECT: "border-red-300 bg-red-50 text-red-800",
};

export default function RecommendationBadge({
  value,
  size = "md",
}: {
  value: Recommendation | string | null | undefined;
  size?: "sm" | "md";
}) {
  const known = value === "APPROVE" || value === "REVIEW" || value === "REJECT";
  const style = known ? STYLES[value] : "border-slate-300 bg-slate-50 text-slate-700";
  const sizing = size === "sm" ? "px-2 py-0.5 text-xs" : "px-3 py-1 text-sm";
  return (
    <span className={`inline-flex items-center rounded-md border font-semibold tracking-wide ${style} ${sizing}`}>
      {known ? value : "NO DECISION"}
    </span>
  );
}
