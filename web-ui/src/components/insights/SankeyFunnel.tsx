import { Bar, BarChart, ResponsiveContainer, XAxis, YAxis } from "recharts";

import type { InsightsOverviewResponse } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

export interface FunnelStage {
  stage: string;
  count: number;
}

const INITIAL_DIMENSION = { width: 600, height: 200 };

/**
 * The six funnel stages (plan: discovered → gated → Stage A → Stage B →
 * apply-verdict → applied). Stage A = totals.evaluated; Stage B = the sum
 * of real verdict counts (the derived below_threshold bucket is a Stage A
 * outcome, not a Stage B one).
 */
export function funnelStages(overview: InsightsOverviewResponse): FunnelStage[] {
  const verdicts = overview.verdict_distribution;
  const stageB = (verdicts["apply"] ?? 0) + (verdicts["consider"] ?? 0) + (verdicts["skip"] ?? 0);
  return [
    { stage: "Discovered", count: overview.totals.jobs },
    { stage: "Gate passed", count: overview.totals.ml_gate_passed },
    { stage: "Stage A", count: overview.totals.evaluated },
    { stage: "Stage B", count: stageB },
    { stage: "Apply verdict", count: verdicts["apply"] ?? 0 },
    { stage: "Applied", count: overview.totals.applied },
  ];
}

/**
 * Linear funnel as a horizontal bar chart. A true Sankey is overkill for
 * a six-stage straight line (no branching flows to show) — the bars read
 * as the same narrowing shape with none of the link-layout machinery.
 * The mono caption repeats every stage value so the numbers survive
 * environments where the SVG can't lay out (and stay copyable).
 */
export function SankeyFunnel({ overview }: { overview: InsightsOverviewResponse }) {
  const stages = funnelStages(overview);
  if (overview.totals.jobs === 0) {
    return (
      <ChartCard title="Funnel">
        <ChartEmpty>No jobs discovered yet — run a scan and the funnel fills in.</ChartEmpty>
      </ChartCard>
    );
  }
  return (
    <ChartCard title="Funnel">
      <div className="h-52" data-testid="funnel-chart">
        {/* initialDimension: a real ResizeObserver corrects it on the
            first frame; environments without one (tests) still render. */}
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
            {/* No entrance animation: load choreography is banned (DESIGN.md). */}
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
