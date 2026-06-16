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

interface RunSubsteps {
  run: string;
  extract_ms: number;
  embed_ms: number;
  predict_ms: number;
}

function aggregate(timings: StepTimingRow[]): RunSubsteps[] {
  const gateSteps = timings.filter((t) => t.step_type === "gate");
  const runs = new Map<string, { extract: number; embed: number; predict: number }>();
  for (const row of gateSteps) {
    if (!runs.has(row.run_id)) {
      runs.set(row.run_id, { extract: 0, embed: 0, predict: 0 });
    }
    const entry = runs.get(row.run_id)!;
    const name = row.step_name.toLowerCase();
    if (name.includes("extract")) {
      entry.extract += row.elapsed_ms;
    } else if (name.includes("embed")) {
      entry.embed += row.elapsed_ms;
    } else if (name.includes("predict")) {
      entry.predict += row.elapsed_ms;
    }
  }
  return [...runs.entries()]
    .map(([id, v]) => ({
      run: id.slice(0, 8),
      extract_ms: v.extract,
      embed_ms: v.embed,
      predict_ms: v.predict,
    }))
    .slice(-20);
}

const SERIES = [
  { key: "extract_ms", label: "extract", color: "rgb(var(--accent))" },
  { key: "embed_ms", label: "embed", color: "rgb(var(--consider))" },
  { key: "predict_ms", label: "predict", color: "rgb(var(--apply))" },
] as const;

export function GateSubstep({ timings }: { timings: StepTimingRow[] }) {
  const data = aggregate(timings);
  if (data.length === 0) {
    return (
      <ChartCard title="Gate substeps">
        <ChartEmpty>No gate substep data in this period.</ChartEmpty>
      </ChartCard>
    );
  }
  return (
    <ChartCard title="Gate substeps">
      <div className="h-52" data-testid="gate-substep">
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
              unit="ms"
            />
            <Tooltip
              contentStyle={{ fontSize: 12, borderRadius: 6 }}
              formatter={(value) => `${Math.round(Number(value))}ms`}
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
