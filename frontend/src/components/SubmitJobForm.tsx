import { useState } from 'react';
import { submitJob } from '../api/jobs';
import type { JobType } from '../api/types';
import './SubmitJobForm.css';

const JOB_TYPES: { value: JobType; label: string; placeholder: string }[] = [
  {
    value: 'ml_inference',
    label: 'ML Inference',
    placeholder: '{\n  "features": [0.5, 1.2, 3.4]\n}',
  },
  {
    value: 'etl_pipeline',
    label: 'ETL Pipeline',
    placeholder: '{\n  "source": "data/spotify_export"\n}',
  },
  {
    value: 'report_generation',
    label: 'Report Generation',
    placeholder: '{\n  "report_type": "monthly_summary",\n  "date_range": {"start": "2026-01-01", "end": "2026-01-31"}\n}',
  },
];

interface SubmitJobFormProps {
  onSubmitted?: (jobId: string) => void;
}

export function SubmitJobForm({ onSubmitted }: SubmitJobFormProps) {
  const [jobType, setJobType] = useState<JobType>('ml_inference');
  const [payloadText, setPayloadText] = useState(JOB_TYPES[0].placeholder);
  const [idempotencyKey, setIdempotencyKey] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successId, setSuccessId] = useState<string | null>(null);

  const selectedType = JOB_TYPES.find((t) => t.value === jobType)!;

  function handleTypeChange(value: JobType) {
    setJobType(value);
    setPayloadText(JOB_TYPES.find((t) => t.value === value)!.placeholder);
    setError(null);
    setSuccessId(null);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSuccessId(null);

    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(payloadText);
    } catch {
      setError('Payload must be valid JSON.');
      return;
    }

    setSubmitting(true);
    try {
      const created = await submitJob(jobType, payload, idempotencyKey.trim() || undefined);
      setSuccessId(created.job_id);
      onSubmitted?.(created.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit job');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="submit-job-form" onSubmit={handleSubmit}>
      <div>
        <label htmlFor="job-type">Job type</label>
        <select
          id="job-type"
          value={jobType}
          onChange={(e) => handleTypeChange(e.target.value as JobType)}
        >
          {JOB_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label htmlFor="job-payload">Payload (JSON)</label>
        <textarea
          id="job-payload"
          rows={6}
          value={payloadText}
          onChange={(e) => setPayloadText(e.target.value)}
          spellCheck={false}
          aria-describedby="job-payload-hint"
        />
        <p id="job-payload-hint" className="submit-job-form__hint">
          Example for {selectedType.label}: <code>{selectedType.placeholder}</code>
        </p>
      </div>

      <div>
        <label htmlFor="idempotency-key">Idempotency key (optional)</label>
        <input
          id="idempotency-key"
          type="text"
          value={idempotencyKey}
          onChange={(e) => setIdempotencyKey(e.target.value)}
          placeholder="e.g. nightly-report-2026-06-08"
        />
      </div>

      <button type="submit" disabled={submitting}>
        {submitting ? 'Submitting…' : 'Submit job'}
      </button>

      {error && <p className="submit-job-form__error">{error}</p>}
      {successId && (
        <p className="submit-job-form__success">
          Job submitted: <code>{successId}</code>
        </p>
      )}
    </form>
  );
}
