import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

export type ChartKind = 'bar' | 'line';

export interface SeriesConfig {
  /** Key in the data objects to plot on the Y axis. */
  dataKey: string;
  /** Human label shown in the tooltip. */
  name?: string;
  /** Hex color, e.g. "#1e5cf5". */
  color?: string;
}

export interface ChartProps {
  data: Record<string, string | number>[];
  /** Key in each datum used for the X axis category. */
  xKey: string;
  series: SeriesConfig[];
  kind?: ChartKind;
  /** Height in px. MUST be set so ResponsiveContainer has a sized parent during SSG. */
  height?: number;
  /** Optional Y-axis label, e.g. "tokens/sec". */
  yLabel?: string;
}

const defaultColors = ['#1e5cf5', '#16a34a', '#ea580c', '#9333ea', '#db2777'];

/**
 * A small wrapper around Recharts for use inside MDX essays.
 * Mount with `client:visible` and an explicit height:
 *   <Chart client:visible data={...} xKey="batch" series={[...]} />
 */
export default function Chart({
  data,
  xKey,
  series,
  kind = 'bar',
  height = 320,
  yLabel,
}: ChartProps) {
  const withColor = series.map((s, i) => ({
    ...s,
    color: s.color ?? defaultColors[i % defaultColors.length],
    name: s.name ?? s.dataKey,
  }));

  return (
    <div style={{ width: '100%', height }} className="not-prose my-2">
      <ResponsiveContainer width="100%" height="100%">
        {kind === 'line' ? (
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" strokeOpacity={0.5} />
            <XAxis dataKey={xKey} tick={{ fontSize: 12 }} stroke="#a1a1aa" />
            <YAxis tick={{ fontSize: 12 }} stroke="#a1a1aa" label={yLabel ? { value: yLabel, angle: -90, position: 'insideLeft' } : undefined} />
            <Tooltip />
            {withColor.map((s) => (
              <Line
                key={s.dataKey}
                type="monotone"
                dataKey={s.dataKey}
                name={s.name}
                stroke={s.color}
                strokeWidth={2}
                dot={{ r: 3 }}
              />
            ))}
          </LineChart>
        ) : (
          <BarChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" strokeOpacity={0.5} />
            <XAxis dataKey={xKey} tick={{ fontSize: 12 }} stroke="#a1a1aa" />
            <YAxis tick={{ fontSize: 12 }} stroke="#a1a1aa" label={yLabel ? { value: yLabel, angle: -90, position: 'insideLeft' } : undefined} />
            <Tooltip />
            {withColor.map((s) => (
              <Bar key={s.dataKey} dataKey={s.dataKey} name={s.name} fill={s.color} radius={[4, 4, 0, 0]} />
            ))}
          </BarChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
