import Box from "@cloudscape-design/components/box";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";

import type { StepTimingRow } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

const STAGES = ["funnel", "evaluation"] as const;
type Stage = (typeof STAGES)[number];

function aggregate(timings: StepTimingRow[]) {
  const totals: Record<Stage, { seconds: number; count: number }> = {
    funnel: { seconds: 0, count: 0 },
    evaluation: { seconds: 0, count: 0 },
  };
  for (const row of timings) {
    if (row.step_type !== "stage" || !STAGES.includes(row.step_name as Stage)) continue;
    const value = totals[row.step_name as Stage];
    value.seconds += row.elapsed_ms / 1000;
    value.count += 1;
  }
  const labels: Record<Stage, string> = {
    funnel: "Filter",
    evaluation: "Evaluation",
  };
  return STAGES.filter((stage) => totals[stage].count > 0).map((stage) => ({
    stage: labels[stage],
    seconds: totals[stage].seconds / totals[stage].count,
    runs: totals[stage].count,
  }));
}

export function EvaluateBreakdown({ timings }: { timings: StepTimingRow[] }) {
  const data = aggregate(timings);
  if (data.length === 0) return <ChartCard title="Evaluation time by step"><ChartEmpty>No evaluation timing data in this time range.</ChartEmpty></ChartCard>;
  return (
    <ChartCard title="Evaluation time by step">
      <SpaceBetween size="xs">
        <Box variant="small" color="text-body-secondary">
          Average per run that recorded this step.
        </Box>
        <Table
          variant="embedded"
          contentDensity="compact"
          wrapLines
          items={data}
          trackBy="stage"
          ariaLabels={{ tableLabel: "Evaluation time by step" }}
          columnDefinitions={[
            { id: "step", header: "Step", width: 100, cell: (row) => row.stage },
            { id: "average", header: "Average", width: 68, cell: (row) => `${row.seconds.toFixed(1)}s` },
            { id: "runs", header: "Runs", width: 42, cell: (row) => row.runs.toLocaleString() },
          ]}
        />
      </SpaceBetween>
    </ChartCard>
  );
}
