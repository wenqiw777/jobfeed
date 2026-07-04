import { CartesianGrid, Line, LineChart, ResponsiveContainer, XAxis, YAxis } from "recharts";

import type { InsightsOverviewResponse } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";
import { zeroFillDaily } from "@/lib/daily-series";
import { dayTick } from "@/lib/dates";

const SERIES = [
  { key: "discovered", label: "discovered", color: "rgb(var(--accent))" },
  { key: "evaluated", label: "evaluated", color: "rgb(var(--consider))" },
  { key: "applied", label: "applied", color: "rgb(var(--apply))" },
] as const;

const INITIAL_DIMENSION = { width: 600, height: 200 };

/**
 * Discovered / evaluated / applied per day. The API series is sparse
 * (only days having data), so it is zero-filled across the whole window
 * client-side — a gap on the axis must read as "nothing happened", not
 * as a missing day. The HTML legend keeps the series names testable
 * without SVG layout.
 */
export function DailyTimeline({ overview }: { overview: InsightsOverviewResponse }) {
  if (overview.daily.length === 0) {
    return (
      <ChartCard title="Daily activity">
        <ChartEmpty>No activity in this window — widen it or run a scan.</ChartEmpty>
      </ChartCard>
    );
  }
  const data = zeroFillDaily(overview.daily, overview.window_days);
  return (
    <ChartCard title="Daily activity">
      <div className="h-52" data-testid="daily-timeline">
        {/* initialDimension: a real ResizeObserver corrects it on the
            first frame; environments without one (tests) still render. */}
        <ResponsiveContainer width="100%" height="100%" initialDimension={INITIAL_DIMENSION}>
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
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
            {SERIES.map(({ key, color }) => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
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
            <span
              aria-hidden="true"
              className="h-0.5 w-3"
              style={{ backgroundColor: color }}
            />
            <span className="text-micro text-mute">{label}</span>
          </li>
        ))}
      </ul>
    </ChartCard>
  );
}
