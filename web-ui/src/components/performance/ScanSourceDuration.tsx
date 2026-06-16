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

/** Group scan step timings by run, one bar per source step. */
function aggregate(timings: StepTimingRow[]): { run: string; duration_s: number }[] {
  const scanSteps = timings.filter((t) => t.step_type === "scan");
  // Sum per run.
  const byRun = new Map<string, number>();
  for (const row of scanSteps) {
    byRun.set(row.run_id, (byRun.get(row.run_id) ?? 0) + row.elapsed_ms);
  }
  return [...byRun.entries()]
    .map(([run, ms]) => ({ run: run.slice(0, 8), duration_s: ms / 1000 }))
    .slice(-20);
}

export function ScanSourceDuration({ timings }: { timings: StepTimingRow[] }) {
  const data = aggregate(timings);
  if (data.length === 0) {
    return (
      <ChartCard title="Scan duration">
        <ChartEmpty>No scan timings in this period.</ChartEmpty>
      </ChartCard>
    );
  }
  return (
    <ChartCard title="Scan duration">
      <div className="h-52" data-testid="scan-source-duration">
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
              unit="s"
            />
            <Tooltip
              contentStyle={{ fontSize: 12, borderRadius: 6 }}
              formatter={(value) => [`${Number(value).toFixed(1)}s`, "duration"]}
            />
            <Bar
              dataKey="duration_s"
              fill="rgb(var(--accent))"
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
