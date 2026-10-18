import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getReport } from "../api/client";
import type { EvaluationReport, EvaluationResult, Job } from "../api/types";
import ResultView from "../components/ResultView";
import { useJobPolling } from "../hooks/useJobPolling";

/** The job result has no per-agent evidence; the saved report does. */
function CompletedJob({ result }: { result: EvaluationResult }) {
  const [report, setReport] = useState<EvaluationReport | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getReport(result.evaluation_id, controller.signal)
      .then(setReport)
      .catch((err) => {
        if (!controller.signal.aborted)
          setReportError(`Per-analysis evidence couldn't be loaded (${err instanceof Error ? err.message : "error"}).`);
      });
    return () => controller.abort();
  }, [result.evaluation_id]);

  return <ResultView result={result} report={report} reportError={reportError} />;
}

function ProgressPanel({ job }: { job: Job | null }) {
  const progress = Math.max(0, Math.min(100, job?.progress ?? 0));
  return (
    <div className="card">
      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold">
          Evaluating {job?.vendor_name ?? "vendor"}
        </h1>
        <span className="text-sm tabular-nums text-slate-600">{progress}%</span>
      </div>
      <div
        className="mt-4 h-2 w-full overflow-hidden rounded-full bg-slate-100"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progress}
        aria-label="Evaluation progress"
      >
        <div className="h-full rounded-full bg-slate-700 transition-all duration-500" style={{ width: `${progress}%` }} />
      </div>
      <p className="mt-3 text-sm text-slate-600" aria-live="polite">
        {job?.current_step ?? "Waiting for the evaluation to start…"}
      </p>
      <p className="mt-1 text-xs text-slate-500">Status: {job?.status ?? "pending"}. This page updates every few seconds.</p>
    </div>
  );
}

export default function JobPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const { job, error, warning } = useJobPolling(jobId);

  if (error) {
    return (
      <div className="mx-auto max-w-2xl">
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-5 text-sm text-red-800">
          <p className="font-medium">{error}</p>
          <Link to="/" className="mt-3 inline-block underline">Start a new evaluation</Link>
        </div>
      </div>
    );
  }

  if (job?.status === "failed") {
    return (
      <div className="mx-auto max-w-2xl">
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-5 text-sm text-red-800">
          <p className="font-medium">The evaluation of {job.vendor_name} failed.</p>
          {job.error && <p className="mt-2 font-mono text-xs">{job.error}</p>}
          <Link to="/" className="mt-3 inline-block underline">Start a new evaluation</Link>
        </div>
      </div>
    );
  }

  if (job?.status === "complete" && job.result) {
    return <CompletedJob result={job.result} />;
  }

  return (
    <div className="mx-auto max-w-2xl space-y-3">
      <ProgressPanel job={job} />
      {warning && <p className="text-sm text-amber-800">{warning}</p>}
    </div>
  );
}
