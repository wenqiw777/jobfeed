import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { StepTimingRow } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

const INITIAL_DIMENSION = { width: 600, height: 200 };

function aggregate(timings: StepTimingRow[]): { run: string; errors: number }[] {
  const errorsOnly = timings.filter((t) => t.is_error);
  const byRun = new Map<string, number>();
  for (const row of errorsOnly) {
    byRun.set(row.run_id, (byRun.get(row.run_id) ?? 0) + 1);
  }
  return [...byRun.entries()]
    .map(([id, count]) => ({ run: id.slice(0, 8), errors: count }))
    .slice(-20);
}

export function ErrorsPerRun({ timings }: { timings: StepTimingRow[] }) {
  const data = aggregate(timings);
  if (data.length === 0) {
    return (
      <ChartCard title="Errors per run">
        <ChartEmpty>No errors in this period.</ChartEmpty>
      </ChartCard>
    );
  }
  return (
    <ChartCard title="Errors per run">
      <div className="h-52" data-testid="errors-per-run">
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
            <Bar
              dataKey="errors"
              fill="rgb(var(--danger))"
              barSize={16}
              radius={[3, 3, 0, 0]}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}
