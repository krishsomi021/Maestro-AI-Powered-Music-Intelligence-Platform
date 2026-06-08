export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

/** Number of recent jobs fetched for client-side queue-statistics aggregation. */
export const QUEUE_STATS_JOB_LIMIT = 500;

/** Number of recent jobs fetched for the execution-history page. */
export const JOB_HISTORY_LIMIT = 100;

/** Number of recent jobs fetched for the monitoring page's recent-jobs list. */
export const RECENT_JOBS_LIMIT = 20;

/** Polling interval (ms) for non-terminal job status. */
export const JOB_POLL_INTERVAL_MS = 3000;
