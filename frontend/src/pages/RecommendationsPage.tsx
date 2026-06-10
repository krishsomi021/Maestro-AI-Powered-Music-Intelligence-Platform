import { useState } from 'react';
import { getJob, submitJob } from '../api/jobs';
import { TERMINAL_JOB_STATUSES, type JobResponse, type RecommenderResult, type TrackSearchResult } from '../api/types';
import { Card } from '../components/Card';
import { JobStatusBadge } from '../components/JobStatusBadge';
import { ListeningAnalytics } from '../components/ListeningAnalytics';
import { RecommendationResults } from '../components/RecommendationResults';
import { SeedTrackPicker } from '../components/SeedTrackPicker';
import { usePolling } from '../hooks/usePolling';
import { JOB_POLL_INTERVAL_MS } from '../constants';
import './RecommendationsPage.css';

const TOP_N = 10;

type RecommenderMode = 'cooccurrence' | 'pgvector';

const RECOMMENDER_OPTIONS: { value: RecommenderMode; label: string; description: string }[] = [
  {
    value: 'cooccurrence',
    label: 'Collaborative (co-occurrence)',
    description: 'Dict-based: recommends tracks that frequently appeared alongside your seeds in listening history.',
  },
  {
    value: 'pgvector',
    label: 'Content-based (pgvector)',
    description: 'Vector similarity: recommends tracks with similar genre/artist profiles using sentence-transformer embeddings stored in Postgres.',
  },
];

export function RecommendationsPage() {
  const [seeds, setSeeds] = useState<TrackSearchResult[]>([]);
  const [recommender, setRecommender] = useState<RecommenderMode>('cooccurrence');
  const [jobId, setJobId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const job = usePolling<JobResponse | null>(
    () => (jobId ? getJob(jobId) : Promise.resolve(null)),
    JOB_POLL_INTERVAL_MS,
    (j) => j === null || TERMINAL_JOB_STATUSES.includes(j.status),
    [jobId],
  );

  async function handleGetRecommendations() {
    if (seeds.length === 0) return;
    setSubmitError(null);
    setSubmitting(true);
    try {
      const created = await submitJob('ml_inference', {
        seed_tracks: seeds.map((s) => s.uri),
        top_n: TOP_N,
        recommender,
      });
      setJobId(created.job_id);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Failed to submit recommendation job');
    } finally {
      setSubmitting(false);
    }
  }

  const recommenderResult =
    job.data?.status === 'complete' ? (job.data.result as unknown as RecommenderResult | null) : null;

  return (
    <div>
      <h1>Spotify Recommendations</h1>
      <p>Pick a few seed tracks and get similarity-based recommendations from your listening history.</p>

      <Card
        title="Get recommendations"
        description="Search for tracks you like, add them as seeds, then submit a recommendation job."
      >
        <fieldset className="recommendations-page__mode-picker">
          <legend>Recommender mode</legend>
          {RECOMMENDER_OPTIONS.map((opt) => (
            <label key={opt.value} className="recommendations-page__mode-option">
              <input
                type="radio"
                name="recommender-mode"
                value={opt.value}
                checked={recommender === opt.value}
                onChange={() => setRecommender(opt.value)}
              />
              <span className="recommendations-page__mode-label">{opt.label}</span>
              <span className="recommendations-page__mode-desc">{opt.description}</span>
            </label>
          ))}
        </fieldset>

        <SeedTrackPicker seeds={seeds} onChange={setSeeds} />

        <div className="recommendations-page__submit">
          <button type="button" onClick={handleGetRecommendations} disabled={seeds.length === 0 || submitting}>
            {submitting ? 'Submitting…' : 'Get recommendations'}
          </button>
          {submitError && <p className="recommendations-page__error">{submitError}</p>}
        </div>

        {job.data && (
          <div className="recommendations-page__job-status">
            <JobStatusBadge status={job.data.status} />
            <code>{job.data.job_id}</code>
          </div>
        )}

        {job.data?.status === 'failed' && job.data.error && (
          <p className="recommendations-page__error">{job.data.error}</p>
        )}

        {recommenderResult && <RecommendationResults result={recommenderResult} />}
      </Card>

      <ListeningAnalytics />
    </div>
  );
}
