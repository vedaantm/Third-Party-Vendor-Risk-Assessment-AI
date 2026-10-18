import { useEffect, useRef, useState } from "react";
import { ApiError, getJob } from "../api/client";
import type { Job } from "../api/types";

export const POLL_INTERVAL_MS = 3000;
export const MAX_CONSECUTIVE_ERRORS = 5;

export interface JobPollingState {
  job: Job | null;
  /** Set when polling stopped without reaching complete/failed. */
  error: string | null;
  /** Set while polling continues after a transient error. */
  warning: string | null;
}

/**
 * Polls GET /jobs/{id} until the job is complete or failed. A transient
 * network error doesn't stop polling; MAX_CONSECUTIVE_ERRORS in a row, or
 * a 404, does.
 */
export function useJobPolling(jobId: string | undefined): JobPollingState {
  const [state, setState] = useState<JobPollingState>({ job: null, error: null, warning: null });
  const failures = useRef(0);

  useEffect(() => {
    if (!jobId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    failures.current = 0;
    setState({ job: null, error: null, warning: null });

    const poll = async () => {
      try {
        const job = await getJob(jobId, controller.signal);
        failures.current = 0;
        setState({ job, error: null, warning: null });
        if (job.status === "complete" || job.status === "failed") return;
      } catch (err) {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 404) {
          setState((s) => ({ ...s, error: "This evaluation job doesn't exist.", warning: null }));
          return;
        }
        failures.current += 1;
        const message = err instanceof Error ? err.message : "Unknown error";
        if (failures.current >= MAX_CONSECUTIVE_ERRORS) {
          setState((s) => ({ ...s, error: `Stopped checking progress: ${message}`, warning: null }));
          return;
        }
        setState((s) => ({ ...s, warning: `Having trouble reaching the server — retrying. (${message})` }));
      }
      timer = setTimeout(poll, POLL_INTERVAL_MS);
    };

    poll();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  return state;
}
