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

const INITIAL_DIMENSION = { width: 600, height: 200 };

function dayTick(day: string): string {
  const [, month, date] = day.split("-");
  return `${Number(month)}/${Number(date)}`;
}

/** The LLM stats row carries token counts and latency; daily cost is
 * approximated from tokens * model price. The backend overview gives the
 * total; here we show per-day shape using avg tokens as a proxy. */
export function DailyCost({ stats }: { stats: LLMDailyStatsRow[] }) {
  if (stats.length === 0) {
    return (
      <ChartCard title="Daily LLM cost proxy">
        <ChartEmpty>No LLM cost data in this period.</ChartEmpty>
      </ChartCard>
    );
  }
  // Proxy: total tokens per day (input + output).
  const data = stats.map((row) => ({
    day: row.day,
    tokens: row.avg_input_tokens + row.avg_output_tokens,
  }));
  return (
    <ChartCard title="Daily LLM cost proxy">
      <div className="h-52" data-testid="daily-cost">
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
              formatter={(value) => [`${Number(value).toLocaleString()} tokens`, "total"]}
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
