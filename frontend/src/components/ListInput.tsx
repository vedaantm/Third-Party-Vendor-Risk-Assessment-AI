import { useId, useState } from "react";

interface Props {
  label: string;
  hint?: string;
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  error?: string;
}

/** Add/remove a list of short phrases (priorities, dealbreakers, …). */
export default function ListInput({ label, hint, values, onChange, placeholder, error }: Props) {
  const [draft, setDraft] = useState("");
  const inputId = useId();

  const add = () => {
    const item = draft.trim();
    if (!item) return;
    if (!values.some((v) => v.toLowerCase() === item.toLowerCase())) onChange([...values, item]);
    setDraft("");
  };

  return (
    <div>
      <label htmlFor={inputId} className="label">
        {label}
      </label>
      {hint && <p className="mb-2 text-xs text-slate-500">{hint}</p>}
      <div className="flex gap-2">
        <input
          id={inputId}
          className={`input ${error ? "input-error" : ""}`}
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <button type="button" className="btn-secondary shrink-0" onClick={add}>
          Add
        </button>
      </div>
      {values.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-2" aria-label={label}>
          {values.map((v) => (
            <li
              key={v}
              className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 py-0.5 pl-3 pr-1 text-sm text-slate-700"
            >
              {v}
              <button
                type="button"
                aria-label={`Remove ${v}`}
                className="rounded-full px-1.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700"
                onClick={() => onChange(values.filter((x) => x !== v))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="mt-1 text-sm text-red-700">{error}</p>}
    </div>
  );
}
