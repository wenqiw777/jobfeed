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

interface RunBreakdown {
  run: string;
  funnel_s: number;
  stage_a_s: number;
  stage_b_s: number;
}

/** The exact evaluate-phase names the backend emits under
 * `step_type="stage"`. Anything else is ignored — never silently
 * folded into a bucket. */
const STAGE_NAMES = ["funnel", "stage_a", "stage_b"] as const;
type StageName = (typeof STAGE_NAMES)[number];

function isStageName(name: string): name is StageName {
  return (STAGE_NAMES as readonly string[]).includes(name);
}

function aggregate(timings: StepTimingRow[]): RunBreakdown[] {
  const stageSteps = timings.filter(
    (t) => t.step_type === "stage" && isStageName(t.step_name),
  );
  const runs = new Map<string, Record<StageName, number>>();
  for (const row of stageSteps) {
    if (!runs.has(row.run_id)) {
      runs.set(row.run_id, { funnel: 0, stage_a: 0, stage_b: 0 });
    }
    const entry = runs.get(row.run_id)!;
    entry[row.step_name as StageName] += row.elapsed_ms;
  }
  return [...runs.entries()]
    .map(([id, v]) => ({
      run: id.slice(0, 8),
      funnel_s: v.funnel / 1000,
      stage_a_s: v.stage_a / 1000,
      stage_b_s: v.stage_b / 1000,
    }))
    .slice(-20);
}

const SERIES = [
  { key: "funnel_s", label: "funnel", color: "rgb(var(--accent))" },
  { key: "stage_a_s", label: "stage A", color: "rgb(var(--consider))" },
  { key: "stage_b_s", label: "stage B", color: "rgb(var(--apply))" },
] as const;

export function EvaluateBreakdown({ timings }: { timings: StepTimingRow[] }) {
  const data = aggregate(timings);
  if (data.length === 0) {
    return (
      <ChartCard title="Evaluate breakdown">
        <ChartEmpty>No evaluate timings in this period.</ChartEmpty>
      </ChartCard>
    );
  }
  return (
    <ChartCard title="Evaluate breakdown">
      <div className="h-52" data-testid="evaluate-breakdown">
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
              formatter={(value) => `${Number(value).toFixed(1)}s`}
            />
            <Legend iconSize={8} wrapperStyle={{ fontSize: 11 }} />
            {SERIES.map(({ key, label, color }) => (
              <Bar
                key={key}
                dataKey={key}
                name={label}
                fill={color}
                barSize={14}
                radius={[3, 3, 0, 0]}
                isAnimationActive={false}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}
