import {
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { FunnelRow } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

const INITIAL_DIMENSION = { width: 600, height: 200 };

interface FunnelStage {
  stage: string;
  count: number;
}

/** Aggregate funnel rows: take the latest run's funnel snapshot or sum
 * across all runs in the window, whichever makes more sense. Here we
 * sum across the window for a holistic view. */
function aggregate(funnel: FunnelRow[]): FunnelStage[] {
  if (funnel.length === 0) return [];
  let candidates = 0;
  let afterFilter = 0;
  let afterGate = 0;
  let scored = 0;
  for (const row of funnel) {
    candidates += row.total_candidates;
    afterFilter += row.after_filter;
    afterGate += row.after_gate;
    scored += row.scored;
  }
  return [
    { stage: "Candidates", count: candidates },
    { stage: "After filter", count: afterFilter },
    { stage: "After gate", count: afterGate },
    { stage: "Scored", count: scored },
  ];
}

export function FunnelConversion({ funnel }: { funnel: FunnelRow[] }) {
  const stages = aggregate(funnel);
  if (stages.length === 0) {
    return (
      <ChartCard title="Funnel conversion">
        <ChartEmpty>No funnel data in this period.</ChartEmpty>
      </ChartCard>
    );
  }
  return (
    <ChartCard title="Funnel conversion">
      <div className="h-52" data-testid="funnel-conversion">
        <ResponsiveContainer width="100%" height="100%" initialDimension={INITIAL_DIMENSION}>
          <BarChart
            data={stages}
            layout="vertical"
            margin={{ top: 4, right: 24, bottom: 4, left: 4 }}
          >
            <XAxis type="number" hide />
            <YAxis
              type="category"
              dataKey="stage"
              width={92}
              axisLine={false}
              tickLine={false}
              tick={{ fill: "rgb(var(--mute))", fontSize: 11 }}
            />
            <Tooltip
              contentStyle={{ fontSize: 12, borderRadius: 6 }}
              formatter={(value) => Number(value).toLocaleString()}
            />
            <Bar
              dataKey="count"
              fill="rgb(var(--accent))"
              barSize={16}
              radius={[0, 3, 3, 0]}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {stages.map(({ stage, count }) => (
          <div key={stage} className="flex items-baseline gap-1">
            <dt className="text-micro text-mute">{stage}</dt>
            <dd className="font-mono text-micro text-ink-2">{count.toLocaleString()}</dd>
          </div>
        ))}
      </dl>
    </ChartCard>
  );
}
