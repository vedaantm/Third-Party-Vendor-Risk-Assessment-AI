import { AGENT_KEYS, type Weights } from "../api/types";

/** Same defaults as routes.EvaluateRequest. */
export const DEFAULT_WEIGHTS: Weights = {
  financial: 0.3,
  compliance: 0.25,
  operational: 0.2,
  reputation: 0.1,
  strategic: 0.15,
};

/** Mirrors routes.WEIGHT_SUM_TOLERANCE. */
export const WEIGHT_SUM_TOLERANCE = 1e-3;

export interface WeightCheck {
  total: number;
  valid: boolean;
  error: string | null;
}

/** Client-side mirror of EvaluateRequest._validate_weights. */
export function checkWeights(weights: Weights): WeightCheck {
  const values = AGENT_KEYS.map((k) => weights[k]);
  const total = values.reduce((sum, v) => sum + (Number.isFinite(v) ? v : 0), 0);
  if (values.some((v) => !Number.isFinite(v))) {
    return { total, valid: false, error: "Every weight needs a number." };
  }
  if (values.some((v) => v < 0)) {
    return { total, valid: false, error: "Weights can't be negative." };
  }
  if (Math.abs(total - 1) > WEIGHT_SUM_TOLERANCE) {
    return { total, valid: false, error: `Weights must add up to 1.00 (currently ${total.toFixed(2)}).` };
  }
  return { total, valid: true, error: null };
}
