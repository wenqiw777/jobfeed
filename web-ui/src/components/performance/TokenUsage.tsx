import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { LLMDailyStatsRow } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";
import { dayTick } from "@/lib/dates";

const INITIAL_DIMENSION = { width: 600, height: 200 };

const SERIES = [
  { key: "avg_input_tokens", label: "avg input", color: "rgb(var(--accent))" },
  { key: "avg_output_tokens", label: "avg output", color: "rgb(var(--consider))" },
] as const;

export function TokenUsage({ stats }: { stats: LLMDailyStatsRow[] }) {
  if (stats.length === 0) {
    return (
      <ChartCard title="Token usage">
        <ChartEmpty>No token data in this period.</ChartEmpty>
      </ChartCard>
    );
  }
  return (
    <ChartCard title="Token usage">
      <div className="h-52" data-testid="token-usage">
        <ResponsiveContainer width="100%" height="100%" initialDimension={INITIAL_DIMENSION}>
          <LineChart data={stats} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
            <CartesianGrid stroke="rgb(var(--hairline))" vertical={false} />
            <XAxis
              dataKey="day"
              tickFormatter={dayTick}
              axisLine={false}
              tickLine={false}
              interval="preserveStartEnd"
              tick={{ fill: "rgb(var(--mute))", fontSize: 11 }}
            />
            <YAxis
              allowDecimals={false}
              axisLine={false}
              tickLine={false}
              tick={{ fill: "rgb(var(--mute))", fontSize: 11 }}
            />
            <Tooltip
              contentStyle={{ fontSize: 12, borderRadius: 6 }}
              formatter={(value) => Math.round(Number(value)).toLocaleString()}
            />
            {SERIES.map(({ key, label, color }) => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
                name={label}
                stroke={color}
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {SERIES.map(({ key, label, color }) => (
          <li key={key} className="flex items-center gap-1.5">
            <span aria-hidden="true" className="h-0.5 w-3" style={{ backgroundColor: color }} />
            <span className="text-micro text-mute">{label}</span>
          </li>
        ))}
      </ul>
    </ChartCard>
  );
}
