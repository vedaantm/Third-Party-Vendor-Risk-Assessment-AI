import { useEffect, useId, useRef, useState } from "react";
import { searchCompanies } from "../api/client";
import type { CompanyMatch } from "../api/types";

interface Props {
  value: string;
  onChange: (value: string) => void;
  error?: string;
}

const DEBOUNCE_MS = 250;

/**
 * Vendor name input with suggestions from GET /companies/search
 * (SEC-registered companies). Free text is allowed — private vendors
 * won't be in the SEC list.
 */
export default function CompanySearch({ value, onChange, error }: Props) {
  const [results, setResults] = useState<CompanyMatch[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [searchError, setSearchError] = useState<string | null>(null);
  const skipNextSearch = useRef(false);
  const listId = useId();
  const inputId = useId();

  useEffect(() => {
    if (skipNextSearch.current) {
      skipNextSearch.current = false;
      return;
    }
    const q = value.trim();
    if (q.length < 2) {
      setResults([]);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const { companies } = await searchCompanies(q, controller.signal);
        setResults(companies);
        setSearchError(null);
        setOpen(true);
        setActive(-1);
      } catch (err) {
        if (!controller.signal.aborted) {
          setResults([]);
          setSearchError("Company search is unavailable — you can still type the vendor name.");
        }
      }
    }, DEBOUNCE_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [value]);

  const choose = (company: CompanyMatch) => {
    skipNextSearch.current = true;
    onChange(company.name);
    setOpen(false);
    setResults([]);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open || results.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && active >= 0) {
      e.preventDefault();
      choose(results[active]!);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  const showList = open && results.length > 0;

  return (
    <div className="relative">
      <label htmlFor={inputId} className="label">
        Vendor
      </label>
      <input
        id={inputId}
        className={`input ${error ? "input-error" : ""}`}
        value={value}
        placeholder="Search SEC-registered companies, or type any name"
        autoComplete="off"
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-invalid={Boolean(error)}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onFocus={() => results.length > 0 && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
      />
      {showList && (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-10 mt-1 max-h-72 w-full overflow-auto rounded-md border border-slate-200 bg-white py-1 shadow-lg"
        >
          {results.map((c, i) => (
            <li
              key={`${c.ticker}-${c.cik}`}
              role="option"
              aria-selected={i === active}
              className={`flex cursor-pointer items-center justify-between px-3 py-2 text-sm ${
                i === active ? "bg-slate-100" : "hover:bg-slate-50"
              }`}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(c);
              }}
            >
              <span className="text-slate-900">{c.name}</span>
              <span className="font-mono text-xs text-slate-500">{c.ticker}</span>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="mt-1 text-sm text-red-700">{error}</p>}
      {!error && searchError && <p className="mt-1 text-xs text-slate-500">{searchError}</p>}
    </div>
  );
}
