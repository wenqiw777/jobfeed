import PieChart from "@cloudscape-design/components/pie-chart";

import type { LLMDailyStatsRow } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

export function TokenUsage({ stats }: { stats: LLMDailyStatsRow[] }) {
  if (stats.length === 0) return <ChartCard title="Overall average token mix per model call"><ChartEmpty>No token data in this time range.</ChartEmpty></ChartCard>;
  const callsFor = (row: LLMDailyStatsRow) => Number.isFinite(row.call_count) && row.call_count > 0 ? row.call_count : 1;
  const callCount = stats.reduce((sum, row) => sum + callsFor(row), 0);
  const data = [
    { type: "Input", value: stats.reduce((sum, row) => sum + row.avg_input_tokens * callsFor(row), 0) / callCount },
    { type: "Output", value: stats.reduce((sum, row) => sum + row.avg_output_tokens * callsFor(row), 0) / callCount },
  ];
  const total = data.reduce((sum, row) => sum + row.value, 0);
  return (
    <ChartCard title="Overall average token mix per model call">
      <PieChart
        ariaLabel="Overall average token mix per model call"
        ariaDescription="Call-count-weighted average input and output tokens per model call."
        variant="donut"
        size="small"
        hideFilter
        innerMetricValue={Math.round(total).toLocaleString()}
        innerMetricDescription="tokens / model call"
        data={data.map((row) => ({ title: row.type, value: row.value }))}
        segmentDescription={(segment) => `${Math.round(segment.value).toLocaleString()} tokens`}
      />
    </ChartCard>
  );
}
