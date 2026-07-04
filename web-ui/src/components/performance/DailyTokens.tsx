import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { LLMDailyStatsRow } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";
import { dayTick } from "@/lib/dates";

const INITIAL_DIMENSION = { width: 600, height: 200 };

/** Daily token usage: average tokens per LLM call (input + output) per
 * day. The API exposes token counts only — no per-day cost — so this
 * panel charts tokens honestly instead of pretending to price them. */
export function DailyTokens({ stats }: { stats: LLMDailyStatsRow[] }) {
  if (stats.length === 0) {
    return (
      <ChartCard title="Daily token usage">
        <ChartEmpty>No LLM token data in this period.</ChartEmpty>
      </ChartCard>
    );
  }
  const data = stats.map((row) => ({
    day: row.day,
    tokens: row.avg_input_tokens + row.avg_output_tokens,
  }));
  return (
    <ChartCard title="Daily token usage">
      <div className="h-52" data-testid="daily-tokens">
        <ResponsiveContainer width="100%" height="100%" initialDimension={INITIAL_DIMENSION}>
          <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
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
              formatter={(value) => [`${Number(value).toLocaleString()} tokens`, "avg per call"]}
            />
            <Bar
              dataKey="tokens"
              fill="rgb(var(--consider))"
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
