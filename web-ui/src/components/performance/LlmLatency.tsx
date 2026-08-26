import Box from "@cloudscape-design/components/box";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";

import type { LLMDailyStatsRow } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

function formatSeconds(value: number): string {
  return `${Number(value.toFixed(1))}s`;
}

export function LlmLatency({ stats }: { stats: LLMDailyStatsRow[] }) {
  if (stats.length === 0) return <ChartCard title="Model response time"><ChartEmpty>No model timing data in this time range.</ChartEmpty></ChartCard>;
  const byModel = new Map<string, { model: string; stage: string | null; calls: number; weight: number; hasCallCount: boolean; median: number; p95: number }>();
  for (const row of stats) {
    const model = row.model?.trim() || "Model unavailable";
    const stage = row.stage ?? null;
    const hasCallCount = Number.isFinite(row.call_count) && row.call_count > 0;
    const weight = hasCallCount ? row.call_count : 1;
    const key = `${model}\u0000${stage ?? ""}`;
    const value = byModel.get(key) ?? { model, stage, calls: 0, weight: 0, hasCallCount: true, median: 0, p95: 0 };
    value.calls += hasCallCount ? row.call_count : 0;
    value.weight += weight;
    value.hasCallCount &&= hasCallCount;
    value.median += row.p50_latency_ms * weight;
    value.p95 += row.p95_latency_ms * weight;
    byModel.set(key, value);
  }
  const data = [...byModel.values()]
    .map((row) => ({ ...row, median: row.median / row.weight / 1000, p95: row.p95 / row.weight / 1000 }))
    .sort((a, b) => b.calls - a.calls);
  const hasMissingCallCounts = data.some((row) => !row.hasCallCount);
  const stageLabel = (stage: string | null) => stage === "a" ? "Quick evaluation" : stage === "b" ? "Detailed review" : "Unspecified stage";
  return (
    <ChartCard title="Model response time">
      <SpaceBetween size="xs">
        <Box variant="small" color="text-body-secondary">
          {hasMissingCallCounts ? "Daily average latency; call counts unavailable from current server." : "Call-weighted average of daily latency."}
        </Box>
        <Table
          variant="embedded"
          contentDensity="compact"
          wrapLines
          items={data}
          trackBy={(row) => `${row.model}-${row.stage ?? "unknown"}`}
          ariaLabels={{ tableLabel: "Model response time" }}
          columnDefinitions={[
            {
              id: "model",
              header: "Model / stage",
              width: 120,
              cell: (row) => <SpaceBetween size="xxs"><Box variant="strong">{row.model}</Box><Box variant="small">{stageLabel(row.stage)}</Box></SpaceBetween>,
            },
            {
              id: "latency",
              header: "Calls / latency",
              width: 100,
              cell: (row) => <SpaceBetween size="xxs"><Box variant="small">{row.hasCallCount ? `${row.calls.toLocaleString()} calls` : "Calls unavailable"}</Box><Box variant="small">Median {formatSeconds(row.median)}</Box><Box variant="small">P95 {formatSeconds(row.p95)}</Box></SpaceBetween>,
            },
          ]}
        />
      </SpaceBetween>
    </ChartCard>
  );
}
