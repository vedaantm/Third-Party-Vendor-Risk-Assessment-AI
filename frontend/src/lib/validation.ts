import type { ValidationErrorItem } from "../api/types";

/**
 * Turn a FastAPI 422 detail list into messages keyed by form field, e.g.
 *   loc ["body", "client_profile", "contract_value"] → "client_profile.contract_value"
 *   loc ["body", "weights"]                          → "weights"
 * Anything that can't be tied to a field is returned under "_form".
 */
export function fieldErrors(items: ValidationErrorItem[]): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const item of items) {
    const path = item.loc[0] === "body" ? item.loc.slice(1) : item.loc;
    // Drop list indexes so "client_profile.priorities.0" maps to the list field
    const key = path.filter((p) => typeof p === "string").join(".") || "_form";
    const message = item.msg.replace(/^Value error, /, "");
    errors[key] = errors[key] ? `${errors[key]} ${message}` : message;
  }
  return errors;
}
