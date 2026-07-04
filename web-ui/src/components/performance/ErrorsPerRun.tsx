import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { RunSummary } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

const INITIAL_DIMENSION = { width: 600, height: 200 };

/** Handled pipeline errors per run, from the runs' `errors` counter.
 * (Step-timing `is_error` only marks timed blocks that raised, so it
 * undercounts handled errors — the run counter is the honest source.)
 * `runs` arrives newest-first from the API; reverse for a left-to-right
 * timeline and keep the quiet zero-error runs off the chart. */
function aggregate(runs: RunSummary[]): { run: string; errors: number }[] {
  return runs
    .filter((r) => r.errors > 0)
    .slice(0, 20)
    .reverse()
    .map((r) => ({ run: r.run_id.slice(0, 8), errors: r.errors }));
}

export function ErrorsPerRun({ runs }: { runs: RunSummary[] }) {
  const data = aggregate(runs);
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
