import { checkWeights, DEFAULT_WEIGHTS } from "./weights";

test("default weights are valid despite floating-point rounding", () => {
  // 0.3 + 0.25 + 0.2 + 0.1 + 0.15 is not exactly 1 in floating point
  expect(checkWeights(DEFAULT_WEIGHTS)).toEqual({ total: expect.closeTo(1, 6), valid: true, error: null });
});

test("weights within the backend tolerance are valid", () => {
  expect(checkWeights({ ...DEFAULT_WEIGHTS, financial: 0.3004 }).valid).toBe(true);
});

test("weights that don't sum to 1 are invalid", () => {
  const check = checkWeights({ ...DEFAULT_WEIGHTS, financial: 0.5 });
  expect(check.valid).toBe(false);
  expect(check.error).toMatch(/add up to 1\.00 \(currently 1\.20\)/);
});

test("negative weights are invalid even when the total is 1", () => {
  const check = checkWeights({ financial: -0.2, compliance: 0.6, operational: 0.2, reputation: 0.2, strategic: 0.2 });
  expect(check.valid).toBe(false);
  expect(check.error).toMatch(/negative/);
});

test("an empty weight is invalid", () => {
  expect(checkWeights({ ...DEFAULT_WEIGHTS, reputation: Number.NaN }).error).toMatch(/needs a number/);
});
