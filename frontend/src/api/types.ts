export type JobType = 'ml_inference' | 'etl_pipeline' | 'report_generation';

export type JobStatus =
  | 'pending'
  | 'running'
  | 'retrying'
  | 'complete'
  | 'failed'
  | 'cancelled';

export const TERMINAL_JOB_STATUSES: readonly JobStatus[] = [
  'complete',
  'failed',
  'cancelled',
];

export interface JobResponse {
  job_id: string;
  job_type: JobType;
  status: JobStatus;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  idempotency_key: string | null;
  retry_count: number;
}

export interface JobCreateResponse {
  job_id: string;
  status: JobStatus;
  created_at: string;
}

export interface JobListResponse {
  jobs: JobResponse[];
  total: number;
}

export interface CancelJobResponse {
  job_id: string;
  status: JobStatus;
}

export interface TopTrackItem {
  track_name: string;
  artist_name: string;
  play_count: number;
  total_ms_played: number;
}

export interface TopArtistItem {
  artist_name: string;
  play_count: number;
  total_ms_played: number;
  unique_tracks: number;
}

export interface ListeningTrendItem {
  month: string;
  play_count: number;
  total_ms_played: number;
}

export interface TrackSearchResult {
  uri: string;
  track_name: string;
  artist_name: string;
}

export interface RecommendationItem {
  uri: string;
  track_name: string;
  artist_name: string;
  score: number;
}

export interface RecommenderResult {
  recommendations: RecommendationItem[];
  seed_count: number;
  model: string;
  top_n: number;
}
