import { useState } from 'react';
import { cancelJob } from '../api/jobs';
import type { JobResponse } from '../api/types';
import { JobStatusBadge } from './JobStatusBadge';
import './JobDetail.css';

const CANCELLABLE_STATUSES = new Set(['pending', 'running', 'retrying']);

interface JobDetailProps {
  job: JobResponse;
  onCancelled?: () => void;
}

function formatTimestamp(value: string | null): string {
  if (!value) return '—';
  return new Date(value).toLocaleString();
}

export function JobDetail({ job, onCancelled }: JobDetailProps) {
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  const canCancel = CANCELLABLE_STATUSES.has(job.status);

  async function handleCancel() {
    setCancelling(true);
    setCancelError(null);
    try {
      await cancelJob(job.job_id);
      onCancelled?.();
    } catch (err) {
      setCancelError(err instanceof Error ? err.message : 'Failed to cancel job');
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div className="job-detail">
      <div className="job-detail__header">
        <div>
          <h3>{job.job_type}</h3>
          <code>{job.job_id}</code>
        </div>
        <JobStatusBadge status={job.status} />
      </div>

      <dl className="job-detail__meta">
        <div>
          <dt>Created</dt>
          <dd>{formatTimestamp(job.created_at)}</dd>
        </div>
        <div>
          <dt>Started</dt>
          <dd>{formatTimestamp(job.started_at)}</dd>
        </div>
        <div>
          <dt>Completed</dt>
          <dd>{formatTimestamp(job.completed_at)}</dd>
        </div>
        <div>
          <dt>Retries</dt>
          <dd>{job.retry_count}</dd>
        </div>
        {job.idempotency_key && (
          <div>
            <dt>Idempotency key</dt>
            <dd>
              <code>{job.idempotency_key}</code>
            </dd>
          </div>
        )}
      </dl>

      <div>
        <h3>Payload</h3>
        <pre>{JSON.stringify(job.payload, null, 2)}</pre>
      </div>

      {job.result && (
        <div>
          <h3>Result</h3>
          <pre>{JSON.stringify(job.result, null, 2)}</pre>
        </div>
      )}

      {job.error && (
        <div>
          <h3>Error</h3>
          <pre className="job-detail__error">{job.error}</pre>
        </div>
      )}

      {canCancel && (
        <div className="job-detail__actions">
          <button type="button" className="danger" onClick={handleCancel} disabled={cancelling}>
            {cancelling ? 'Cancelling…' : 'Cancel job'}
          </button>
          {cancelError && <p className="job-detail__error-text">{cancelError}</p>}
        </div>
      )}
    </div>
  );
}
