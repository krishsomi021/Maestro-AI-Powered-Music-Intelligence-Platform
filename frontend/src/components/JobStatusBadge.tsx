import type { JobStatus } from '../api/types';
import './JobStatusBadge.css';

const STATUS_LABELS: Record<JobStatus, string> = {
  pending: 'Pending',
  running: 'Running',
  retrying: 'Retrying',
  complete: 'Complete',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return (
    <span className={`job-status-badge job-status-badge--${status}`}>
      {STATUS_LABELS[status]}
    </span>
  );
}
