import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, submitEvaluation } from "../api/client";
import type { EvaluateRequest, Weights } from "../api/types";
import CompanySearch from "../components/CompanySearch";
import ListInput from "../components/ListInput";
import WeightsEditor from "../components/WeightsEditor";
import { fieldErrors } from "../lib/validation";
import { checkWeights, DEFAULT_WEIGHTS } from "../lib/weights";

const INDUSTRIES = [
  "Software", "IT Services", "Cybersecurity", "Financial Services", "Healthcare",
  "Manufacturing", "Retail", "Logistics", "Energy", "Telecommunications", "Government",
];

interface FormState {
  vendorName: string;
  companyName: string;
  industry: string;
  contractValue: string;
  contractYears: string;
  riskAppetite: string;
  priorities: string[];
  dealbreakers: string[];
  niceToHave: string[];
  weights: Weights;
}

const INITIAL: FormState = {
  vendorName: "",
  companyName: "",
  industry: "",
  contractValue: "",
  contractYears: "3",
  riskAppetite: "medium",
  priorities: [],
  dealbreakers: [],
  niceToHave: [],
  weights: DEFAULT_WEIGHTS,
};

/** Client-side checks that mirror routes.EvaluateRequest / ClientProfile. */
function localErrors(f: FormState): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!f.vendorName.trim()) errors.vendor_name = "Enter the vendor to evaluate.";
  if (!f.companyName.trim()) errors["client_profile.company_name"] = "Enter your company name.";
  if (!f.industry.trim()) errors["client_profile.industry"] = "Enter your industry.";
  const value = Number(f.contractValue);
  if (f.contractValue.trim() === "" || !Number.isFinite(value) || value <= 0)
    errors["client_profile.contract_value"] = "Enter a contract value greater than 0.";
  const years = Number(f.contractYears);
  if (!Number.isInteger(years) || years < 1) errors["client_profile.contract_years"] = "Enter a whole number of years (1 or more).";
  const weights = checkWeights(f.weights);
  if (!weights.valid) errors.weights = weights.error!;
  return errors;
}

function toRequest(f: FormState): EvaluateRequest {
  return {
    vendor_name: f.vendorName.trim(),
    client_profile: {
      company_name: f.companyName.trim(),
      industry: f.industry.trim(),
      contract_value: Number(f.contractValue),
      contract_years: Number(f.contractYears),
      risk_appetite: f.riskAppetite,
      priorities: f.priorities,
      dealbreakers: f.dealbreakers,
      nice_to_have: f.niceToHave,
    },
    weights: f.weights,
  };
}

function FieldError({ message }: { message?: string }) {
  return message ? <p className="mt-1 text-sm text-red-700">{message}</p> : null;
}

export default function EvaluatePage() {
  const navigate = useNavigate();
  const [form, setForm] = useState<FormState>(INITIAL);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [showErrors, setShowErrors] = useState(false);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((f) => ({ ...f, [key]: value }));

  const shownErrors = showErrors ? { ...localErrors(form), ...errors } : errors;
  const err = (key: string) => shownErrors[key];

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setShowErrors(true);
    setErrors({});
    if (Object.keys(localErrors(form)).length > 0) return;

    setSubmitting(true);
    try {
      const accepted = await submitEvaluation(toRequest(form));
      navigate(`/jobs/${accepted.job_id}`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 422) {
        setErrors(fieldErrors(error.validation));
      } else {
        setErrors({ _form: error instanceof Error ? error.message : "Something went wrong." });
      }
      setSubmitting(false);
    }
  };

  // Server errors for nested fields not shown inline (e.g. list items)
  const otherServerErrors = Object.entries(errors).filter(
    ([key]) =>
      ![
        "vendor_name", "client_profile.company_name", "client_profile.industry",
        "client_profile.contract_value", "client_profile.contract_years", "client_profile.risk_appetite",
        "client_profile.priorities", "client_profile.dealbreakers", "client_profile.nice_to_have", "weights",
      ].includes(key),
  );

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-semibold tracking-tight">Evaluate a vendor</h1>
      <p className="mt-1 text-sm text-slate-600">
        Runs the financial, compliance, operational, reputation and strategic analyses. This usually takes 2–3 minutes.
      </p>

      <form className="mt-6 space-y-6" onSubmit={onSubmit} noValidate>
        {otherServerErrors.length > 0 && (
          <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
            <p className="font-medium">The server rejected the request:</p>
            <ul className="mt-1 list-disc pl-5">
              {otherServerErrors.map(([key, msg]) => (
                <li key={key}>{key === "_form" ? msg : `${key}: ${msg}`}</li>
              ))}
            </ul>
          </div>
        )}

        <section className="card space-y-4">
          <h2 className="section-title">Vendor</h2>
          <CompanySearch value={form.vendorName} onChange={(v) => set("vendorName", v)} error={err("vendor_name")} />
        </section>

        <section className="card space-y-4">
          <h2 className="section-title">Your organisation</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="company-name" className="label">Company name</label>
              <input id="company-name" className={`input ${err("client_profile.company_name") ? "input-error" : ""}`}
                value={form.companyName} onChange={(e) => set("companyName", e.target.value)} />
              <FieldError message={err("client_profile.company_name")} />
            </div>
            <div>
              <label htmlFor="industry" className="label">Industry</label>
              <input id="industry" list="industry-options" className={`input ${err("client_profile.industry") ? "input-error" : ""}`}
                value={form.industry} onChange={(e) => set("industry", e.target.value)} placeholder="e.g. Financial Services" />
              <datalist id="industry-options">
                {INDUSTRIES.map((i) => <option key={i} value={i} />)}
              </datalist>
              <FieldError message={err("client_profile.industry")} />
            </div>
            <div>
              <label htmlFor="contract-value" className="label">Contract value (USD)</label>
              <input id="contract-value" type="number" min={0} step={1000} inputMode="decimal"
                className={`input tabular-nums ${err("client_profile.contract_value") ? "input-error" : ""}`}
                value={form.contractValue} onChange={(e) => set("contractValue", e.target.value)} />
              <FieldError message={err("client_profile.contract_value")} />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label htmlFor="contract-years" className="label">Term (years)</label>
                <input id="contract-years" type="number" min={1} step={1}
                  className={`input tabular-nums ${err("client_profile.contract_years") ? "input-error" : ""}`}
                  value={form.contractYears} onChange={(e) => set("contractYears", e.target.value)} />
                <FieldError message={err("client_profile.contract_years")} />
              </div>
              <div>
                <label htmlFor="risk-appetite" className="label">Risk appetite</label>
                <select id="risk-appetite" className="input" value={form.riskAppetite}
                  onChange={(e) => set("riskAppetite", e.target.value)}>
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </select>
                <FieldError message={err("client_profile.risk_appetite")} />
              </div>
            </div>
          </div>
        </section>

        <section className="card space-y-5">
          <h2 className="section-title">Requirements</h2>
          <ListInput label="Priorities" hint="What matters most in this vendor."
            placeholder="e.g. 24/7 enterprise support" values={form.priorities}
            onChange={(v) => set("priorities", v)} error={err("client_profile.priorities")} />
          <ListInput label="Dealbreakers" hint="Any of these, if confirmed, rejects the vendor."
            placeholder="e.g. no SOC 2 Type 2 report" values={form.dealbreakers}
            onChange={(v) => set("dealbreakers", v)} error={err("client_profile.dealbreakers")} />
          <ListInput label="Nice to have" placeholder="e.g. ISO 27001 certified" values={form.niceToHave}
            onChange={(v) => set("niceToHave", v)} error={err("client_profile.nice_to_have")} />
        </section>

        <section className="card">
          <WeightsEditor weights={form.weights} onChange={(w) => set("weights", w)}
            serverError={errors.weights} />
        </section>

        <div className="flex justify-end">
          <button type="submit" className="btn-primary" disabled={submitting}>
            {submitting ? "Starting…" : "Start evaluation"}
          </button>
        </div>
      </form>
    </div>
  );
}
