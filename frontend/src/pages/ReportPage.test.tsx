import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { EvaluationReport } from "../api/types";
import { ROUTER_FUTURE } from "../routerFuture";
import { json, mockFetch, on } from "../test/fetchMock";
import { evaluationReport } from "../test/fixtures";
import ReportPage from "./ReportPage";

function renderReport(report: EvaluationReport | Response) {
  mockFetch(
    on("GET", "/api/v1/evaluations/eval-1a2b3c4d/report", () =>
      report instanceof Response ? report : json(200, report)),
  );
  render(
    <MemoryRouter future={ROUTER_FUTURE} initialEntries={["/evaluations/eval-1a2b3c4d/report"]}>
      <Routes>
        <Route path="/evaluations/:evaluationId/report" element={<ReportPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test("shows the saved report: recommendation, scores, evidence, weights", async () => {
  renderReport(evaluationReport());
  expect(await screen.findByRole("heading", { name: "Okta", level: 1 })).toBeInTheDocument();
  expect(screen.getByText(/Evaluated for Acme Corp/)).toBeInTheDocument();
  expect(screen.getByText("REVIEW")).toBeInTheDocument();
  expect(screen.getByText("82.4")).toBeInTheDocument();
  expect(screen.getByText("current_ratio 1.8 [Yahoo Finance]")).toBeInTheDocument();
  expect(screen.getByText("0.30")).toBeInTheDocument();
});

test("OFAC is not stored on reports, so it reads as needing manual screening", async () => {
  renderReport(evaluationReport());
  const banner = await screen.findByLabelText("OFAC sanctions screening");
  expect(banner).toHaveAttribute("data-ofac-tone", "screen");
  expect(banner).toHaveTextContent("Needs manual screening");
  expect(banner).toHaveTextContent("Saved reports don't include the OFAC screening result");
});

test("agents marked 'not run' become an unavailable warning and N/A scores", async () => {
  const report = evaluationReport();
  report.agent_breakdown.compliance = { status: "not run" };
  renderReport(report);
  expect(await screen.findByTestId("unavailable-agents")).toHaveTextContent("1 analysis did not complete: Compliance");
  expect(screen.getAllByText("N/A")).toHaveLength(1);
  expect(screen.getByText("Not run — no result from this analysis.")).toBeInTheDocument();
});

test("truncated agents are detected from the report's red flags", async () => {
  renderReport(evaluationReport({
    red_flags: ["[STRATEGIC] Agent output was truncated — red flags may be incomplete"],
  }));
  expect(await screen.findByTestId("truncated-agents")).toHaveTextContent("Output cut off for: Strategic fit");
});

test("unknown evaluation", async () => {
  renderReport(json(404, { detail: "Evaluation not found" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Evaluation not found.");
});
