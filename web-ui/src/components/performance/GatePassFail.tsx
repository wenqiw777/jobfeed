import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { FunnelRow } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

const INITIAL_DIMENSION = { width: 600, height: 200 };

interface GateRun {
  run: string;
  pass: number;
  fail: number;
}

/** ML-gate pass/fail per evaluate run, derived from the funnel snapshots:
 * jobs enter the gate after the hard filter, so pass = after_gate and
 * fail = after_filter − after_gate. (Step-timing `is_error` means a timed
 * block raised — it never represents gate rejections.) */
function aggregate(funnel: FunnelRow[]): GateRun[] {
  return funnel
    .map((row) => ({
      run: row.run_id.slice(0, 8),
      pass: row.after_gate,
      fail: Math.max(0, row.after_filter - row.after_gate),
    }))
    .slice(-20);
}

export function GatePassFail({ funnel }: { funnel: FunnelRow[] }) {
  const data = aggregate(funnel);
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
              dataKey="pass"
              stackId="gate"
              fill="rgb(var(--apply))"
              barSize={16}
              isAnimationActive={false}
            />
            <Bar
              dataKey="fail"
              stackId="gate"
              fill="rgb(var(--danger))"
              barSize={16}
              radius={[3, 3, 0, 0]}
              isAnimationActive={false}
            />
          </BarChart>
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
