import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { EvaluationResult } from "../api/types";
import { ROUTER_FUTURE } from "../routerFuture";
import { evaluationReport, evaluationResult } from "../test/fixtures";
import ResultView from "./ResultView";

function renderResult(result: EvaluationResult, report = evaluationReport()) {
  render(
    <MemoryRouter future={ROUTER_FUTURE}>
      <ResultView result={result} report={report} reportError={null} />
    </MemoryRouter>,
  );
}

test("shows recommendation, headline scores and the five agent scores", () => {
  renderResult(evaluationResult());
  expect(screen.getByText("REVIEW")).toBeInTheDocument();
  expect(screen.getByText("82.4")).toBeInTheDocument(); // composite
  expect(screen.getByText("78.6")).toBeInTheDocument(); // fit
  expect(screen.getByText("88%")).toBeInTheDocument();  // confidence
  const agents = screen.getByRole("list", { name: "Agent scores" });
  expect(within(agents).getAllByRole("listitem")).toHaveLength(5);
  expect(within(agents).getByText("84.0")).toBeInTheDocument();
});

test("summary cards are labelled Composite, Fit score and Confidence", () => {
  renderResult(evaluationResult());
  expect(screen.getAllByRole("term").map((t) => t.textContent)).toEqual(["Composite", "Fit score", "Confidence"]);
  const fit = screen.getByText("Fit score").closest("div")!;
  expect(fit).toHaveTextContent("78.6");
  expect(fit).toHaveTextContent("Strategic, operational and reputation, out of 100");
});

test("an unavailable agent score shows N/A and the warning", () => {
  renderResult(
    evaluationResult({
      unavailable_agents: ["operational"],
      agent_scores: { financial: 84, compliance: 88, operational: null, reputation: 76, strategic: 81 },
    }),
  );
  expect(within(screen.getByRole("list", { name: "Agent scores" })).getByText("N/A")).toBeInTheDocument();
  expect(screen.getByTestId("unavailable-agents")).toHaveTextContent("Operational");
});

test("truncation is read directly from truncated_agents", () => {
  // No truncation red flag — the warning must come from the field itself
  renderResult(evaluationResult({ truncated_agents: ["financial", "strategic"], red_flags: [] }));
  expect(screen.getByTestId("truncated-agents")).toHaveTextContent("Output cut off for: Financial, Strategic fit");
});

test("an explicit empty truncated_agents shows no warning", () => {
  renderResult(evaluationResult({ truncated_agents: [] }));
  expect(screen.queryByTestId("truncated-agents")).not.toBeInTheDocument();
});

test("job results stored before the field existed fall back to red flags", () => {
  const legacy = evaluationResult({
    red_flags: ["[FINANCIAL] Agent output was truncated — red flags may be incomplete"],
  });
  delete legacy.truncated_agents;
  renderResult(legacy);
  expect(screen.getByTestId("truncated-agents")).toHaveTextContent("Output cut off for: Financial");
});

test("shows red flags with their agent, cert tiers and regulations", () => {
  renderResult(evaluationResult());
  const flags = screen.getByRole("list", { name: "Red flags" });
  expect(flags).toHaveTextContent("Reputation");
  expect(flags).toHaveTextContent("Data breach reported in 2023 [Reuters]");
  const table = screen.getByRole("table");
  expect(within(table).getByText("SOC2_Type2").closest("tr")).toHaveTextContent("Verified");
  expect(within(table).getByText("ISO27001").closest("tr")).toHaveTextContent("Corroborated");
  expect(screen.getByText(/GDPR, CCPA/)).toBeInTheDocument();
});

test("shows certification and per-agent evidence", () => {
  renderResult(evaluationResult());
  expect(screen.getByText(/10-K states SOC 2 Type II report/)).toBeInTheDocument();
  expect(screen.getByText("current_ratio 1.8 [Yahoo Finance]")).toBeInTheDocument();
  expect(screen.getByText("[Reuters] Okta discloses breach")).toBeInTheDocument();
});

test("OFAC possible match with entries is shown, never as clear", () => {
  renderResult(evaluationResult({ ofac_status: "possible_match", ofac_matches: ["ROSNEFT TRADING S.A. (SDN #18299)"] }));
  const banner = screen.getByLabelText("OFAC sanctions screening");
  expect(banner).toHaveTextContent("Needs manual screening");
  expect(banner).toHaveTextContent("SDN #18299");
});

test("missing report shows a note instead of agent evidence", () => {
  render(
    <MemoryRouter future={ROUTER_FUTURE}>
      <ResultView result={evaluationResult()} report={null} reportError="Per-analysis evidence couldn't be loaded (404)." />
    </MemoryRouter>,
  );
  expect(screen.getByText(/Per-analysis evidence couldn't be loaded/)).toBeInTheDocument();
});
