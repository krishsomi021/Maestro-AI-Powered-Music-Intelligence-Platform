import { useCallback, useState } from 'react';
import { getJob, listDeadLetterJobs, listJobs } from '../api/jobs';
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../api/types';
import { Card } from '../components/Card';
import { JobDetail } from '../components/JobDetail';
import { JobTable } from '../components/JobTable';
import { SubmitJobForm } from '../components/SubmitJobForm';
import { usePolling } from '../hooks/usePolling';
import { JOB_POLL_INTERVAL_MS, RECENT_JOBS_LIMIT } from '../constants';

export function JobMonitoringPage() {
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);

  const bumpRefresh = useCallback(() => setRefreshNonce((n) => n + 1), []);

  const recentJobs = usePolling(
    () => listJobs(RECENT_JOBS_LIMIT),
    JOB_POLL_INTERVAL_MS,
    () => false,
    [refreshNonce],
  );

  const deadLetterJobs = usePolling(
    () => listDeadLetterJobs(RECENT_JOBS_LIMIT),
    JOB_POLL_INTERVAL_MS * 4,
    () => false,
    [refreshNonce],
  );

  const selectedJob = usePolling<JobResponse | null>(
    () => (selectedJobId ? getJob(selectedJobId) : Promise.resolve(null)),
    JOB_POLL_INTERVAL_MS,
    (job) => job === null || TERMINAL_JOB_STATUSES.includes(job.status),
    [selectedJobId],
  );

  function handleSubmitted(jobId: string) {
    setSelectedJobId(jobId);
    bumpRefresh();
  }

  function handleSelect(job: JobResponse) {
    setSelectedJobId(job.job_id);
  }

  return (
    <div>
      <h1>Job Monitoring</h1>
      <p>Submit jobs, watch their progress live, and cancel pending or running work.</p>

      <Card title="Submit a job" description="Choose a job type and provide its JSON payload.">
        <SubmitJobForm onSubmitted={handleSubmitted} />
      </Card>

      <Card title="Recent jobs" description={`The ${RECENT_JOBS_LIMIT} most recently created jobs, polled live.`}>
        {recentJobs.error && <p>Failed to load jobs: {recentJobs.error.message}</p>}
        {recentJobs.data && (
          <JobTable
            jobs={recentJobs.data.jobs}
            selectedJobId={selectedJobId}
            onSelect={handleSelect}
          />
        )}
      </Card>

      {selectedJob.data && (
        <Card title="Job detail">
          <JobDetail
            job={selectedJob.data}
            onCancelled={() => {
              bumpRefresh();
            }}
          />
        </Card>
      )}

      <Card
        title="Dead-letter queue"
        description="Jobs that permanently failed after exhausting all retries."
      >
        {deadLetterJobs.error && <p>Failed to load dead-letter jobs: {deadLetterJobs.error.message}</p>}
        {deadLetterJobs.data && (
          <JobTable
            jobs={deadLetterJobs.data.jobs}
            selectedJobId={selectedJobId}
            onSelect={handleSelect}
          />
        )}
      </Card>
    </div>
  );
}
