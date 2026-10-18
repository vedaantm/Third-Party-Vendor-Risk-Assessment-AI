import type {
  CompanyMatch,
  EvaluateAccepted,
  EvaluateRequest,
  EvaluationReport,
  EvaluationSummary,
  Job,
  ValidationErrorItem,
} from "./types";

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
const API = `${API_BASE_URL}/api/v1`;

/** A non-2xx response. `validation` is set for 422s. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly validation: ValidationErrorItem[] = [],
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) },
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, `Could not reach the VendorIQ API at ${API_BASE_URL}.`);
  }

  if (response.ok) return (await response.json()) as T;

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    /* non-JSON error body */
  }
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (response.status === 422 && Array.isArray(detail)) {
    throw new ApiError(422, "Some fields are invalid.", detail as ValidationErrorItem[]);
  }
  const message = typeof detail === "string" ? detail : `Request failed (${response.status}).`;
  throw new ApiError(response.status, message);
}

export function searchCompanies(q: string, signal?: AbortSignal) {
  return request<{ companies: CompanyMatch[] }>(`/companies/search?q=${encodeURIComponent(q)}`, { signal });
}

export function submitEvaluation(body: EvaluateRequest) {
  return request<EvaluateAccepted>("/evaluate", { method: "POST", body: JSON.stringify(body) });
}

export function getJob(jobId: string, signal?: AbortSignal) {
  return request<Job>(`/jobs/${encodeURIComponent(jobId)}`, { signal });
}

/** The backend caps limit at 100. */
export function listEvaluations(limit = 50, signal?: AbortSignal) {
  return request<{ evaluations: EvaluationSummary[] }>(`/evaluations?limit=${limit}`, { signal });
}

export function getReport(evaluationId: string, signal?: AbortSignal) {
  return request<EvaluationReport>(`/evaluations/${encodeURIComponent(evaluationId)}/report`, { signal });
}
