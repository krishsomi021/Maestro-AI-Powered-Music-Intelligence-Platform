import type { JobResponse } from '../api/types';
import { JobStatusBadge } from './JobStatusBadge';
import './JobTable.css';

interface JobTableProps {
  jobs: JobResponse[];
  selectedJobId?: string | null;
  onSelect: (job: JobResponse) => void;
}

function shortId(jobId: string): string {
  return jobId.slice(0, 8);
}

export function JobTable({ jobs, selectedJobId, onSelect }: JobTableProps) {
  if (jobs.length === 0) {
    return <p className="job-table__empty">No jobs to show.</p>;
  }

  return (
    <table className="job-table">
      <thead>
        <tr>
          <th>Job ID</th>
          <th>Type</th>
          <th>Status</th>
          <th>Created</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((job) => (
          <tr
            key={job.job_id}
            className={job.job_id === selectedJobId ? 'job-table__row--selected' : ''}
          >
            <td>
              <code>{shortId(job.job_id)}</code>
            </td>
            <td>{job.job_type}</td>
            <td>
              <JobStatusBadge status={job.status} />
            </td>
            <td>{new Date(job.created_at).toLocaleString()}</td>
            <td>
              <button type="button" className="secondary" onClick={() => onSelect(job)}>
                View
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
