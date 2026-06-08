import { useMemo, useState } from 'react';
import { listJobs } from '../api/jobs';
import type { JobResponse, JobStatus, JobType } from '../api/types';
import { Card } from '../components/Card';
import { JobDetail } from '../components/JobDetail';
import { JobTable } from '../components/JobTable';
import { usePolling } from '../hooks/usePolling';
import { JOB_HISTORY_LIMIT, JOB_POLL_INTERVAL_MS } from '../constants';
import './JobHistoryPage.css';

const STATUS_OPTIONS: (JobStatus | 'all')[] = [
  'all',
  'pending',
  'running',
  'retrying',
  'complete',
  'failed',
  'cancelled',
];

const TYPE_OPTIONS: (JobType | 'all')[] = ['all', 'ml_inference', 'etl_pipeline', 'report_generation'];

export function JobHistoryPage() {
  const [statusFilter, setStatusFilter] = useState<JobStatus | 'all'>('all');
  const [typeFilter, setTypeFilter] = useState<JobType | 'all'>('all');
  const [selectedJob, setSelectedJob] = useState<JobResponse | null>(null);

  const { data, error, loading } = usePolling(
    () => listJobs(JOB_HISTORY_LIMIT),
    JOB_POLL_INTERVAL_MS * 4,
    () => false,
    [],
  );

  const filteredJobs = useMemo(() => {
    if (!data) return [];
    return data.jobs.filter((job) => {
      if (statusFilter !== 'all' && job.status !== statusFilter) return false;
      if (typeFilter !== 'all' && job.job_type !== typeFilter) return false;
      return true;
    });
  }, [data, statusFilter, typeFilter]);

  return (
    <div>
      <h1>Job Execution History</h1>
      <p>
        Browse the {JOB_HISTORY_LIMIT} most recently created jobs and filter by type or status.
      </p>

      <Card title="Filters">
        <div className="job-history__filters">
          <div>
            <label htmlFor="status-filter">Status</label>
            <select
              id="status-filter"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as JobStatus | 'all')}
            >
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s === 'all' ? 'All statuses' : s}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="type-filter">Job type</label>
            <select
              id="type-filter"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value as JobType | 'all')}
            >
              {TYPE_OPTIONS.map((t) => (
                <option key={t} value={t}>
                  {t === 'all' ? 'All types' : t}
                </option>
              ))}
            </select>
          </div>
        </div>
      </Card>

      <Card title="Jobs" description={`${filteredJobs.length} of ${data?.jobs.length ?? 0} loaded jobs match`}>
        {error && <p>Failed to load jobs: {error.message}</p>}
        {loading && !data && <p>Loading…</p>}
        {data && (
          <JobTable
            jobs={filteredJobs}
            selectedJobId={selectedJob?.job_id}
            onSelect={setSelectedJob}
          />
        )}
      </Card>

      {selectedJob && (
        <Card title="Job detail">
          <JobDetail job={selectedJob} />
        </Card>
      )}
    </div>
  );
}
