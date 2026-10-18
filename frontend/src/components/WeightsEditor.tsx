import { AGENT_KEYS, type Weights } from "../api/types";
import { AGENT_LABELS } from "../lib/agents";
import { checkWeights, DEFAULT_WEIGHTS } from "../lib/weights";

interface Props {
  weights: Weights;
  onChange: (weights: Weights) => void;
  serverError?: string;
}

export default function WeightsEditor({ weights, onChange, serverError }: Props) {
  const check = checkWeights(weights);
  const error = check.error ?? serverError;

  return (
    <fieldset>
      <legend className="label">Agent weights</legend>
      <p className="mb-3 text-xs text-slate-500">How much each analysis counts toward the composite. Must add up to 1.00.</p>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        {AGENT_KEYS.map((key) => (
          <div key={key}>
            <label htmlFor={`weight-${key}`} className="mb-1 block text-xs text-slate-600">
              {AGENT_LABELS[key]}
            </label>
            <input
              id={`weight-${key}`}
              type="number"
              inputMode="decimal"
              min={0}
              max={1}
              step={0.05}
              className={`input tabular-nums ${error ? "input-error" : ""}`}
              value={Number.isFinite(weights[key]) ? weights[key] : ""}
              onChange={(e) =>
                onChange({ ...weights, [key]: e.target.value === "" ? Number.NaN : Number(e.target.value) })
              }
            />
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center justify-between text-sm">
        <span className={check.valid ? "text-slate-600" : "font-medium text-red-700"} data-testid="weights-total">
          Total: {check.total.toFixed(2)}
        </span>
        <button type="button" className="text-sm text-slate-600 underline-offset-2 hover:underline" onClick={() => onChange(DEFAULT_WEIGHTS)}>
          Reset to defaults
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-1 text-sm text-red-700">
          {error}
        </p>
      )}
    </fieldset>
  );
}
