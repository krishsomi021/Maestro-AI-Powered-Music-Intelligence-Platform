import { apiDelete, apiGet, apiPost } from './client';
import type {
  CancelJobResponse,
  JobCreateResponse,
  JobListResponse,
  JobResponse,
  JobType,
} from './types';

export function listJobs(limit: number): Promise<JobListResponse> {
  return apiGet<JobListResponse>(`/jobs?limit=${limit}`);
}

export function getJob(jobId: string): Promise<JobResponse> {
  return apiGet<JobResponse>(`/jobs/${jobId}`);
}

export function listDeadLetterJobs(limit: number): Promise<JobListResponse> {
  return apiGet<JobListResponse>(`/jobs/dead-letter?limit=${limit}`);
}

export function submitJob(
  jobType: JobType,
  payload: Record<string, unknown>,
  idempotencyKey?: string,
): Promise<JobCreateResponse> {
  return apiPost<JobCreateResponse>('/jobs', {
    job_type: jobType,
    payload,
    idempotency_key: idempotencyKey || null,
  });
}

export function cancelJob(jobId: string): Promise<CancelJobResponse> {
  return apiDelete<CancelJobResponse>(`/jobs/${jobId}`);
}
