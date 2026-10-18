import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { Job } from "../api/types";
import { MAX_CONSECUTIVE_ERRORS, POLL_INTERVAL_MS } from "../hooks/useJobPolling";
import { json, mockFetch } from "../test/fetchMock";
import { completedJob } from "../test/fixtures";
import JobPage from "./JobPage";
import { ROUTER_FUTURE } from "../routerFuture";

function job(overrides: Partial<Job>): Job {
  return {
    job_id: "job-1", vendor_name: "Okta", status: "running", current_step: null, progress: 0,
    created_at: "2026-10-17T10:00:00+00:00", updated_at: "2026-10-17T10:00:00+00:00", ...overrides,
  };
}

/** Serve a sequence of job states (or errors), one per poll. */
function serveJobs(...steps: (Job | Error | Response)[]) {
  let i = 0;
  return mockFetch((url) => {
    if (url.pathname !== "/api/v1/jobs/job-1") return undefined;
    const step = steps[Math.min(i++, steps.length - 1)]!;
    if (step instanceof Error) throw step;
    if (step instanceof Response) return step;
    return json(200, step);
  });
}

function renderJob() {
  render(
    <MemoryRouter future={ROUTER_FUTURE} initialEntries={["/jobs/job-1"]}>
      <Routes>
        <Route path="/jobs/:jobId" element={<JobPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function nextPoll() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
  });
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("shows progress and step, then stops polling when complete", async () => {
  const fetchMock = serveJobs(
    job({ status: "running", progress: 40, current_step: "Running financial analysis agent..." }),
    completedJob(),
  );
  renderJob();
  await act(async () => {});
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "40");
  expect(screen.getByText("Running financial analysis agent...")).toBeInTheDocument();

  await nextPoll();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  expect(screen.getAllByText(/Okta/).length).toBeGreaterThan(0);
  const callsAtCompletion = fetchMock.mock.calls.filter(([u]) => String(u).includes("/jobs/")).length;
  await nextPoll();
  await nextPoll();
  expect(fetchMock.mock.calls.filter(([u]) => String(u).includes("/jobs/")).length).toBe(callsAtCompletion);
});

test("shows the error when the job failed", async () => {
  serveJobs(job({ status: "failed", progress: 15, error: "RuntimeError: yfinance exploded" }));
  renderJob();
  await act(async () => {});
  expect(screen.getByRole("alert")).toHaveTextContent("The evaluation of Okta failed.");
  expect(screen.getByRole("alert")).toHaveTextContent("RuntimeError: yfinance exploded");
});

test("keeps polling through a transient network error", async () => {
  serveJobs(new TypeError("Failed to fetch"), job({ progress: 66, current_step: "Running operational analysis agent..." }));
  renderJob();
  await act(async () => {});
  expect(screen.getByText(/Having trouble reaching the server/)).toBeInTheDocument();
  await nextPoll();
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "66");
  expect(screen.queryByText(/Having trouble reaching the server/)).not.toBeInTheDocument();
});

test(`stops after ${MAX_CONSECUTIVE_ERRORS} consecutive errors`, async () => {
  serveJobs(new TypeError("Failed to fetch"));
  renderJob();
  await act(async () => {});
  for (let i = 1; i < MAX_CONSECUTIVE_ERRORS; i++) await nextPoll();
  expect(screen.getByRole("alert")).toHaveTextContent(/Stopped checking progress/);
});

test("an unknown job id is reported, not polled forever", async () => {
  serveJobs(json(404, { detail: "Job job-1 not found" }));
  renderJob();
  await act(async () => {});
  expect(screen.getByRole("alert")).toHaveTextContent("This evaluation job doesn't exist.");
});
