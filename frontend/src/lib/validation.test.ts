import { fieldErrors } from "./validation";

// Shapes copied from real backend 422 responses (FastAPI + pydantic v2)
test("maps weights validator errors to the weights field", () => {
  expect(
    fieldErrors([
      {
        type: "value_error",
        loc: ["body", "weights"],
        msg: "Value error, weights must sum to 1.0, got 4.5000",
        input: {},
        ctx: { error: {} },
      },
    ]),
  ).toEqual({ weights: "weights must sum to 1.0, got 4.5000" });
});

test("maps nested client_profile fields and list indexes", () => {
  expect(
    fieldErrors([
      { type: "float_parsing", loc: ["body", "client_profile", "contract_value"], msg: "Input should be a valid number" },
      { type: "string_type", loc: ["body", "client_profile", "priorities", 0], msg: "Input should be a valid string" },
    ]),
  ).toEqual({
    "client_profile.contract_value": "Input should be a valid number",
    "client_profile.priorities": "Input should be a valid string",
  });
});

test("missing body field and errors without a field", () => {
  expect(
    fieldErrors([
      { type: "missing", loc: ["body", "client_profile"], msg: "Field required" },
      { type: "json_invalid", loc: ["body"], msg: "JSON decode error" },
    ]),
  ).toEqual({ client_profile: "Field required", _form: "JSON decode error" });
});
