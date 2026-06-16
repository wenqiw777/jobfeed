import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { StepTimingRow } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

const INITIAL_DIMENSION = { width: 600, height: 200 };

const SOURCE_COLORS: Record<string, string> = {
  scan: "rgb(var(--accent))",
  evaluate: "rgb(var(--consider))",
  gate: "rgb(var(--apply))",
  funnel: "rgb(var(--skip))",
};

function sourceColor(source: string): string {
  return SOURCE_COLORS[source] ?? "rgb(var(--mute))";
}

interface RunErrors {
  run: string;
  [source: string]: string | number;
}

function aggregate(timings: StepTimingRow[]): { data: RunErrors[]; sources: string[] } {
  const errors = timings.filter((t) => t.is_error);
  const allSources = new Set<string>();
  const byRun = new Map<string, Map<string, number>>();
  for (const row of errors) {
    allSources.add(row.step_type);
    if (!byRun.has(row.run_id)) byRun.set(row.run_id, new Map());
    const entry = byRun.get(row.run_id)!;
    entry.set(row.step_type, (entry.get(row.step_type) ?? 0) + 1);
  }
  const sources = [...allSources].sort();
  const data: RunErrors[] = [...byRun.entries()]
    .map(([id, counts]) => {
      const row: RunErrors = { run: id.slice(0, 8) };
      for (const src of sources) row[src] = counts.get(src) ?? 0;
      return row;
    })
    .slice(-20);
  return { data, sources };
}

export function PerSourceErrors({ timings }: { timings: StepTimingRow[] }) {
  const { data, sources } = aggregate(timings);
  if (data.length === 0) {
    return (
      <ChartCard title="Errors by source">
        <ChartEmpty>No errors in this period.</ChartEmpty>
      </ChartCard>
    );
  }
  return (
    <ChartCard title="Errors by source">
      <div className="h-52" data-testid="per-source-errors">
        <ResponsiveContainer width="100%" height="100%" initialDimension={INITIAL_DIMENSION}>
          <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
            <CartesianGrid stroke="rgb(var(--hairline))" vertical={false} />
            <XAxis
              dataKey="run"
              axisLine={false}
              tickLine={false}
              tick={{ fill: "rgb(var(--mute))", fontSize: 11 }}
            />
            <YAxis
              allowDecimals={false}
              axisLine={false}
              tickLine={false}
              tick={{ fill: "rgb(var(--mute))", fontSize: 11 }}
            />
            <Tooltip contentStyle={{ fontSize: 12, borderRadius: 6 }} />
            <Legend iconSize={8} wrapperStyle={{ fontSize: 11 }} />
            {sources.map((src) => (
              <Bar
                key={src}
                dataKey={src}
                stackId="errors"
                fill={sourceColor(src)}
                barSize={16}
                isAnimationActive={false}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}
