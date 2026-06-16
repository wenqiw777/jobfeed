import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { StepTimingRow } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

const INITIAL_DIMENSION = { width: 600, height: 200 };

interface GateDay {
  day: string;
  pass: number;
  fail: number;
}

function dayTick(day: string): string {
  const [, month, date] = day.split("-");
  return `${Number(month)}/${Number(date)}`;
}

function aggregate(timings: StepTimingRow[]): GateDay[] {
  const gateSteps = timings.filter((t) => t.step_type === "gate");
  const byDay = new Map<string, { pass: number; fail: number }>();
  for (const row of gateSteps) {
    const day = row.created_at.slice(0, 10);
    if (!byDay.has(day)) byDay.set(day, { pass: 0, fail: 0 });
    const entry = byDay.get(day)!;
    if (row.is_error) {
      entry.fail += 1;
    } else {
      entry.pass += 1;
    }
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, { pass, fail }]) => ({ day, pass, fail }));
}

export function GatePassFail({ timings }: { timings: StepTimingRow[] }) {
  const data = aggregate(timings);
  if (data.length === 0) {
    return (
      <ChartCard title="Gate pass / fail">
        <ChartEmpty>No gate data in this period.</ChartEmpty>
      </ChartCard>
    );
  }
  return (
    <ChartCard title="Gate pass / fail">
      <div className="h-52" data-testid="gate-pass-fail">
        <ResponsiveContainer width="100%" height="100%" initialDimension={INITIAL_DIMENSION}>
          <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
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
            <Tooltip contentStyle={{ fontSize: 12, borderRadius: 6 }} />
            <Area
              type="monotone"
              dataKey="pass"
              stackId="1"
              fill="rgb(var(--apply))"
              stroke="rgb(var(--apply))"
              fillOpacity={0.4}
              isAnimationActive={false}
            />
            <Area
              type="monotone"
              dataKey="fail"
              stackId="1"
              fill="rgb(var(--danger))"
              stroke="rgb(var(--danger))"
              fillOpacity={0.4}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {[
          { key: "pass", color: "rgb(var(--apply))" },
          { key: "fail", color: "rgb(var(--danger))" },
        ].map(({ key, color }) => (
          <li key={key} className="flex items-center gap-1.5">
            <span aria-hidden="true" className="h-2 w-2 rounded-pill" style={{ backgroundColor: color }} />
            <span className="text-micro text-mute">{key}</span>
          </li>
        ))}
      </ul>
    </ChartCard>
  );
}
