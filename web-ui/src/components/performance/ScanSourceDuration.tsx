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

/** Average fetch duration per source. The backend writes one
 * `step_type="source_fetch"` row per source per scan run, with
 * `step_name` carrying the source name. */
function aggregate(timings: StepTimingRow[]): { source: string; duration_s: number }[] {
  const fetchSteps = timings.filter((t) => t.step_type === "source_fetch");
  const bySource = new Map<string, { total_ms: number; count: number }>();
  for (const row of fetchSteps) {
    const entry = bySource.get(row.step_name) ?? { total_ms: 0, count: 0 };
    entry.total_ms += row.elapsed_ms;
    entry.count += 1;
    bySource.set(row.step_name, entry);
  }
  return [...bySource.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([source, { total_ms, count }]) => ({
      source,
      duration_s: total_ms / count / 1000,
    }));
}

export function ScanSourceDuration({ timings }: { timings: StepTimingRow[] }) {
  const data = aggregate(timings);
  if (data.length === 0) {
    return (
      <ChartCard title="Scan duration by source">
        <ChartEmpty>No scan timings in this period.</ChartEmpty>
      </ChartCard>
    );
  }
  return (
    <ChartCard title="Scan duration by source">
      <div className="h-52" data-testid="scan-source-duration">
        <ResponsiveContainer width="100%" height="100%" initialDimension={INITIAL_DIMENSION}>
          <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
            <CartesianGrid stroke="rgb(var(--hairline))" vertical={false} />
            <XAxis
              dataKey="source"
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
              formatter={(value) => [`${Number(value).toFixed(1)}s`, "avg duration"]}
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
