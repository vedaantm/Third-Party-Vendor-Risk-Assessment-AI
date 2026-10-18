import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { EvaluationSummary } from "../api/types";
import { ROUTER_FUTURE } from "../routerFuture";
import { json, mockFetch, on } from "../test/fetchMock";
import HistoryPage from "./HistoryPage";

function summary(overrides: Partial<EvaluationSummary>): EvaluationSummary {
  return {
    id: "u1", vendor_name: "Okta", company_name: "Acme Corp", evaluation_id: "eval-1",
    scores: { financial: 84, compliance: 88, operational: 79, reputation: 76, strategic: 81, composite: 82.4, risk: 80.1, fit: 78.6 },
    confidence: 0.88, recommendation: "REVIEW", red_flags: [], created_at: "2026-10-17T10:02:30+00:00",
    ...overrides,
  };
}

function renderHistory() {
  render(
    <MemoryRouter future={ROUTER_FUTURE} initialEntries={["/history"]}>
      <Routes>
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/evaluations/:evaluationId/report" element={<p>report page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test("lists evaluations with recommendation, scores and a report link", async () => {
  const fetchMock = mockFetch(
    on("GET", "/api/v1/evaluations", () =>
      json(200, {
        evaluations: [
          summary({}),
          summary({ evaluation_id: "eval-2", vendor_name: "Rosneft Oil Company", recommendation: "REJECT",
                    scores: { ...summary({}).scores, composite: 12.5 }, confidence: null }),
        ],
      })),
  );
  const user = userEvent.setup();
  renderHistory();

  const rows = await screen.findAllByRole("row");
  expect(rows).toHaveLength(3); // header + 2
  expect(within(rows[1]!).getByText("REVIEW")).toBeInTheDocument();
  expect(within(rows[1]!).getByText("82.4")).toBeInTheDocument();
  expect(within(rows[2]!).getByText("REJECT")).toBeInTheDocument();
  expect(within(rows[2]!).getByText("—")).toBeInTheDocument(); // null confidence
  expect(String(fetchMock.mock.calls[0]![0])).toContain("limit=50");

  await user.click(screen.getByRole("link", { name: "Report for Okta" }));
  expect(screen.getByText("report page")).toBeInTheDocument();
});

test("empty history", async () => {
  mockFetch(on("GET", "/api/v1/evaluations", () => json(200, { evaluations: [] })));
  renderHistory();
  expect(await screen.findByText(/No evaluations yet/)).toBeInTheDocument();
});

test("API error", async () => {
  mockFetch(on("GET", "/api/v1/evaluations", () => json(500, { detail: "Internal Server Error" })));
  renderHistory();
  expect(await screen.findByRole("alert")).toHaveTextContent("Internal Server Error");
});
