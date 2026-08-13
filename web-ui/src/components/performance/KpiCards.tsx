import Box from "@cloudscape-design/components/box";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";

import type { PerformanceOverviewResponse } from "@/api/queries";

function formatDuration(ms: number) { return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`; }
function formatCost(usd: number) { return `$${usd.toFixed(2)}`; }
function formatPct(rate: number) { return `${(rate * 100).toFixed(1)}%`; }

/** Delta label; cost and error increases are negative operational signals. */
function DeltaArrow({ delta, goodDirection }: { delta: number | null; goodDirection: "down" | "neutral" }) {
  if (delta === null) return null;
  const isUp = delta > 0;
  const abs = Math.abs(delta) * 100;
  const pct = abs >= 1 ? `${Math.round(abs)}%` : `${abs.toFixed(1)}%`;
  const colorClass = goodDirection === "down" ? (isUp ? "text-danger" : "text-apply") : "text-mute";
  return <span className={`font-mono text-micro ${colorClass}`}>{isUp ? "↑" : "↓"}{pct}</span>;
}

/** Cloudscape KPI summary for operational performance. */
export function KpiCards({ overview }: { overview: PerformanceOverviewResponse }) {
  const items = [
    { label: "Avg scan", value: formatDuration(overview.avg_scan_duration_ms), delta: overview.scan_duration_delta, goodDirection: "neutral" as const },
    { label: "Avg eval", value: formatDuration(overview.avg_eval_duration_ms), delta: overview.eval_duration_delta, goodDirection: "neutral" as const },
    { label: "LLM cost", value: formatCost(overview.total_llm_cost_usd), delta: overview.cost_delta, goodDirection: "down" as const },
    { label: "Error rate", value: formatPct(overview.error_rate), delta: overview.error_rate_delta, goodDirection: "down" as const },
  ];
  return (
    <section aria-label="Performance KPIs">
      <Container header={<Header variant="h2">Performance KPIs</Header>}>
        <ColumnLayout columns={4} minColumnWidth={160} borders="vertical">
          {items.map(({ label, value, delta, goodDirection }) => (
            <div key={label}>
              <Box variant="awsui-key-label">{label}</Box>
              <Box variant="awsui-value-large" fontWeight="bold">{value}</Box>
              <DeltaArrow delta={delta} goodDirection={goodDirection} />
            </div>
          ))}
        </ColumnLayout>
      </Container>
    </section>
  );
}
