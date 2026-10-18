import type { OfacStatus } from "../api/types";

interface Props {
  status: OfacStatus | string | null | undefined;
  matches?: string[] | null;
  /** Shown when status is null, e.g. saved reports that don't store it. */
  missingNote?: string;
}

type Tone = "clear" | "match" | "screen";

const TONE_CLASSES: Record<Tone, string> = {
  clear: "border-emerald-200 bg-emerald-50 text-emerald-900",
  match: "border-red-300 bg-red-50 text-red-900",
  screen: "border-amber-300 bg-amber-50 text-amber-900",
};

/**
 * Sanctions screening result. Only "clear" is ever shown as clear —
 * possible_match, unavailable, null and any unrecognised value all read
 * as needing manual screening (or, for "match", as a confirmed match).
 */
export default function OfacBanner({ status, matches, missingNote }: Props) {
  let tone: Tone;
  let title: string;
  let detail: string;

  switch (status) {
    case "clear":
      tone = "clear";
      title = "OFAC screening: clear";
      detail = "The vendor name did not match any entry on the OFAC SDN list.";
      break;
    case "match":
      tone = "match";
      title = "OFAC sanctions match";
      detail = "The vendor name matches an entry on the OFAC SDN list. Do not engage this vendor.";
      break;
    case "possible_match":
      tone = "screen";
      title = "Needs manual screening — possible OFAC match";
      detail =
        "The vendor name overlaps SDN list entries, or matches one exactly but is too short or generic to " +
        "confirm on name alone. A compliance officer must confirm or clear it.";
      break;
    case "unavailable":
      tone = "screen";
      title = "Needs manual screening — OFAC screening not completed";
      detail = "The SDN list could not be checked. Screen this vendor manually before proceeding.";
      break;
    default:
      tone = "screen";
      title = "Needs manual screening — OFAC status not reported";
      detail = missingNote ?? "No sanctions screening result is available. Screen this vendor manually.";
  }

  const list = matches ?? [];

  return (
    <section
      role={tone === "clear" ? "status" : "alert"}
      aria-label="OFAC sanctions screening"
      data-ofac-tone={tone}
      className={`rounded-lg border p-4 ${TONE_CLASSES[tone]}`}
    >
      <h2 className="text-sm font-semibold">{title}</h2>
      <p className="mt-1 text-sm">{detail}</p>
      {list.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-semibold uppercase tracking-wide opacity-80">SDN entries</p>
          <ul className="mt-1 space-y-1 text-sm" aria-label="OFAC SDN matches">
            {list.map((m) => (
              <li key={m} className="font-mono text-xs leading-relaxed">
                {m}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
