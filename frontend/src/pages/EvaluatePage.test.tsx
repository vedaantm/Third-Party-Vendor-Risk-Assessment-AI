import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { json, mockFetch, on } from "../test/fetchMock";
import EvaluatePage from "./EvaluatePage";
import { ROUTER_FUTURE } from "../routerFuture";

function renderPage() {
  render(
    <MemoryRouter future={ROUTER_FUTURE} initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<EvaluatePage />} />
        <Route path="/jobs/:jobId" element={<p>job page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const companySearch = on("GET", "/api/v1/companies/search", () =>
  json(200, { companies: [{ name: "Okta, Inc.", ticker: "OKTA", cik: "0001660134" }] }),
);

async function fillValidForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Vendor"), "Okt");
  await user.click(await screen.findByRole("option", { name: /Okta, Inc\./ }));
  await user.type(screen.getByLabelText("Company name"), "Acme Corp");
  await user.type(screen.getByLabelText("Industry"), "Financial Services");
  await user.type(screen.getByLabelText("Contract value (USD)"), "500000");
  await user.type(screen.getByLabelText("Priorities"), "24/7 support{Enter}");
  await user.type(screen.getByLabelText("Dealbreakers"), "no SOC 2 Type 2 report{Enter}");
}

afterEach(() => vi.unstubAllGlobals());

test("submits the exact request body the backend expects and opens the job", async () => {
  const fetchMock = mockFetch(
    companySearch,
    on("POST", "/api/v1/evaluate", () =>
      json(202, { job_id: "job-abc", evaluation_id: "eval-1", vendor_name: "Okta, Inc.", status: "pending",
                  message: "", poll_url: "/api/v1/jobs/job-abc" })),
  );
  const user = userEvent.setup();
  renderPage();
  await fillValidForm(user);
  await user.click(screen.getByRole("button", { name: "Start evaluation" }));

  expect(await screen.findByText("job page")).toBeInTheDocument();
  const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(post[1]!.body as string)).toEqual({
    vendor_name: "Okta, Inc.",
    client_profile: {
      company_name: "Acme Corp", industry: "Financial Services", contract_value: 500000,
      contract_years: 3, risk_appetite: "medium", priorities: ["24/7 support"],
      dealbreakers: ["no SOC 2 Type 2 report"], nice_to_have: [],
    },
    weights: { financial: 0.3, compliance: 0.25, operational: 0.2, reputation: 0.1, strategic: 0.15 },
  });
});

test("shows 422 validation errors from the server next to the fields", async () => {
  mockFetch(
    companySearch,
    on("POST", "/api/v1/evaluate", () =>
      json(422, {
        detail: [
          { type: "value_error", loc: ["body", "weights"], msg: "Value error, weights must sum to 1.0, got 1.0500" },
          { type: "float_parsing", loc: ["body", "client_profile", "contract_value"], msg: "Input should be a valid number" },
          { type: "extra", loc: ["body", "client_profile", "something_else"], msg: "Unexpected field" },
        ],
      })),
  );
  const user = userEvent.setup();
  renderPage();
  await fillValidForm(user);
  await user.click(screen.getByRole("button", { name: "Start evaluation" }));

  expect(await screen.findByText("weights must sum to 1.0, got 1.0500")).toBeInTheDocument();
  expect(screen.getByText("Input should be a valid number")).toBeInTheDocument();
  expect(screen.getByText(/client_profile\.something_else: Unexpected field/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Start evaluation" })).toBeEnabled();
});

test("does not submit when weights don't sum to 1", async () => {
  const fetchMock = mockFetch(companySearch);
  const user = userEvent.setup();
  renderPage();
  await fillValidForm(user);
  const financial = screen.getByLabelText("Financial");
  await user.clear(financial);
  await user.type(financial, "0.5");

  expect(screen.getByTestId("weights-total")).toHaveTextContent("Total: 1.20");
  await user.click(screen.getByRole("button", { name: "Start evaluation" }));
  expect(screen.getByRole("alert")).toHaveTextContent(/must add up to 1\.00/);
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});

test("requires vendor, company, industry and contract value", async () => {
  const fetchMock = mockFetch(companySearch);
  const user = userEvent.setup();
  renderPage();
  await user.click(screen.getByRole("button", { name: "Start evaluation" }));
  expect(screen.getByText("Enter the vendor to evaluate.")).toBeInTheDocument();
  expect(screen.getByText("Enter your company name.")).toBeInTheDocument();
  expect(screen.getByText("Enter your industry.")).toBeInTheDocument();
  expect(screen.getByText("Enter a contract value greater than 0.")).toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
});

test("reports an unreachable API", async () => {
  mockFetch(companySearch, on("POST", "/api/v1/evaluate", () => {
    throw new TypeError("Failed to fetch");
  }));
  const user = userEvent.setup();
  renderPage();
  await fillValidForm(user);
  await user.click(screen.getByRole("button", { name: "Start evaluation" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/Could not reach the VendorIQ API/));
});
