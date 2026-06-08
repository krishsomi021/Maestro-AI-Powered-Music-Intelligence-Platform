import { useMemo } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { listJobs } from '../api/jobs';
import type { JobResponse, JobStatus, JobType } from '../api/types';
import { Card } from '../components/Card';
import { usePolling } from '../hooks/usePolling';
import { JOB_POLL_INTERVAL_MS, QUEUE_STATS_JOB_LIMIT } from '../constants';

const STATUS_COLORS: Record<JobStatus, string> = {
  pending: '#b9770e',
  retrying: '#b9770e',
  running: '#2563eb',
  complete: '#1f8a4c',
  failed: '#c0392b',
  cancelled: '#5b6270',
};

function countBy<T extends string>(jobs: JobResponse[], key: 'status' | 'job_type'): Record<T, number> {
  const counts = {} as Record<T, number>;
  for (const job of jobs) {
    const value = job[key] as T;
    counts[value] = (counts[value] ?? 0) + 1;
  }
  return counts;
}

export function QueueStatisticsPage() {
  const { data, error, loading } = usePolling(
    () => listJobs(QUEUE_STATS_JOB_LIMIT),
    JOB_POLL_INTERVAL_MS * 4,
    () => false,
    [],
  );

  const statusData = useMemo(() => {
    if (!data) return [];
    const counts = countBy<JobStatus>(data.jobs, 'status');
    return Object.entries(counts).map(([status, count]) => ({
      status: status as JobStatus,
      count: count as number,
    }));
  }, [data]);

  const typeData = useMemo(() => {
    if (!data) return [];
    const counts = countBy<JobType>(data.jobs, 'job_type');
    return Object.entries(counts).map(([job_type, count]) => ({
      job_type,
      count: count as number,
    }));
  }, [data]);

  return (
    <div>
      <h1>Queue Statistics</h1>
      <p>
        Based on the most recent {QUEUE_STATS_JOB_LIMIT} jobs — not the full historical total. For
        live worker/queue internals, see{' '}
        <a href="http://localhost:5555" target="_blank" rel="noreferrer">
          Flower
        </a>
        .
      </p>

      {error && <p>Failed to load jobs: {error.message}</p>}
      {loading && !data && <p>Loading…</p>}

      {data && (
        <>
          <Card title="Jobs by status" description={`${data.jobs.length} jobs sampled`}>
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie
                  data={statusData}
                  dataKey="count"
                  nameKey="status"
                  outerRadius={100}
                  label={(props: { payload?: { status: JobStatus; count: number } }) => {
                    const entry = props.payload;
                    return entry ? `${entry.status}: ${entry.count}` : '';
                  }}
                >
                  {statusData.map((entry) => (
                    <Cell key={entry.status} fill={STATUS_COLORS[entry.status]} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </Card>

          <Card title="Jobs by type" description={`${data.jobs.length} jobs sampled`}>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={typeData}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                <XAxis dataKey="job_type" />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="count" fill="var(--color-accent)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </>
      )}
    </div>
  );
}
