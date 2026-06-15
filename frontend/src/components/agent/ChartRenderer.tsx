import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { ChartSpec } from '../../hooks/useAgentStream';
import './ChartRenderer.css';

interface Props {
  spec: ChartSpec | null | undefined;
}

export function ChartRenderer({ spec }: Props) {
  if (!spec) return null;

  // Convert parallel x/y arrays to the {name, value} shape recharts expects.
  const data = spec.x.map((label, i) => ({ name: label, value: spec.y[i] ?? 0 }));

  return (
    <div className="chart-renderer">
      <p className="chart-renderer__title">{spec.title}</p>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={data} margin={{ top: 4, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis dataKey="name" tick={{ fontSize: 11 }} />
          <YAxis allowDecimals={false} tick={{ fontSize: 11 }} width={40} />
          <Tooltip />
          <Bar dataKey="value" fill="var(--color-accent)" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
